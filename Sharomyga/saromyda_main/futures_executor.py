# trade_bot_fixed/futures_executor.py
import os
import csv
import json
import time
import random
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException

from . import config as cfg
from .exchange_utils import (
    fmt_qty_str,
    fmt_price_str,
    quantize_price,
    min_notional_qty,
    quantize_qty,
)

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

class FuturesExecutor:
    """
    Binance Futures:
      - открытие/закрытие позиций, постановка SL/TP (reduce-only)
      - реестр позиций (persist)
      - запись сделок в CSV
    """
    def __init__(self, client: Client, logger, state_dir: str = "state"):
        self.client = client
        self.log = logger
        self._ex_info_cache: Dict[str, Any] = {}
        self.fee_taker = float(getattr(cfg, "FEE_TAKER", 0.0004))

        # stats
        self.enabled = bool(getattr(cfg, "STATS_ENABLED", True))
        stats_dir = getattr(cfg, "STATS_DIR", "stats")
        os.makedirs(stats_dir, exist_ok=True)
        self._stats_path = os.path.join(stats_dir, "trades.csv")
        self._ensure_stats_file()

        # state (pos registry)
        os.makedirs(state_dir, exist_ok=True)
        self._state_path = os.path.join(state_dir, "pos_registry.json")
        self.pos_registry: Dict[str, Dict[str, Any]] = {}
        self._load_pos_registry()

    # ---------------- stats utils ----------------
    def _ensure_stats_file(self):
        if not os.path.exists(self._stats_path):
            with open(self._stats_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["ts","symbol","side","qty","entry","exit","reason","pnl_usd"])

    def _load_pos_registry(self):
        try:
            if os.path.exists(self._state_path):
                with open(self._state_path, "r", encoding="utf-8") as f:
                    self.pos_registry = json.load(f)
        except Exception as e:
            self.log.warning(f"[STATE] load registry warn: {e}")
            self.pos_registry = {}

    def _persist_pos_registry(self):
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(self.pos_registry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log.warning(f"[STATE] persist registry warn: {e}")

    # ---------------- symbol setup ----------------
    def ensure_symbol_mode(self, symbol: str, leverage: int = None, margin_type: str = "ISOLATED"):
        try:
            if leverage is None:
                leverage = int(getattr(cfg, "LEVERAGE", 2))
            try:
                self.client.futures_change_leverage(symbol=symbol, leverage=leverage, recvWindow=10000)
            except BinanceAPIException as e:
                if e.code not in (-4046,):
                    raise
            try:
                self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type, recvWindow=10000)
            except BinanceAPIException as e:
                if e.code not in (-4046, -4065, -4047):
                    raise
        except Exception as e:
            self.log.debug(f"[MODE] {symbol} leverage/margin warn: {e}")

    # ---------------- open/close ----------------
    def open_position(self, symbol: str, side: str, wallet_usdt: float, mark_price: float,
                      atr_now: float, atr_smooth: float, fraction: float = 1.0) -> bool:
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            self.log.error(f"[OPEN] bad side {side}")
            return False

        # live available balance
        try:
            acc = self.client.futures_account(recvWindow=10000) or {}
            avail_live = float(acc.get("availableBalance", wallet_usdt))
        except Exception:
            avail_live = wallet_usdt

        taker_buf = float(getattr(cfg, "TAKER_FEE_BUFFER_PCT", 0.001))  # 0.1%
        base = max(avail_live * (1.0 - taker_buf), 0.0)
        pos_frac = float(getattr(cfg, "POSITION_MARGIN_MAX_FRAC", 0.15))
        lev = int(getattr(cfg, "LEVERAGE", 2))
        notional_cap = max(base * pos_frac * lev * float(fraction), 0.0)
        if notional_cap <= 0:
            self.log.warning("[OPEN] notional_cap <= 0")
            return False

        price = float(mark_price or 0.0)
        min_qty = min_notional_qty(self.client, symbol, price)
        qty_raw = max(notional_cap / max(price, 1e-12), min_qty)
        qty = quantize_qty(self.client, symbol, qty_raw)
        if qty <= 0:
            self.log.warning("[OPEN] qty <= 0 after quantize")
            return False
        qty_s = fmt_qty_str(self.client, symbol, qty)

        # ensure leverage/margin
        self.ensure_symbol_mode(symbol)

        side_ord = "BUY" if side == "LONG" else "SELL"

        def _place(qty_val: float):
            qty_s2 = fmt_qty_str(self.client, symbol, qty_val)
            return self.client.futures_create_order(
                symbol=symbol,
                side=side_ord,
                type="MARKET",
                quantity=qty_s2,
                newClientOrderId=self._gen_client_order_id(f"open-{symbol.lower()}"),
                recvWindow=10000,
            )

        # retry shrink on -2019
        shrinks = [1.0, 0.97, 0.94, 0.91]
        order = None
        for s in shrinks:
            try:
                order = self._with_retry(lambda: _place(qty * s))
                if order:
                    qty = quantize_qty(self.client, symbol, qty * s)
                    break
            except BinanceAPIException as e:
                if e.code == -2019:
                    continue
                raise
        if not order:
            return False

        # фактический entry
        entry_px = self._fetch_entry_price(symbol) or price

        # записываем в реестр
        self.pos_registry[symbol] = {
            "side": side,
            "entry_price": float(entry_px),
            "qty": float(qty),
            "sl_id": None,
            "tp_id": None,
            "opened_ms": int(time.time() * 1000),
        }
        self._persist_pos_registry()

        # выставляем SL/TP
        self.set_sl_tp(symbol, side, float(entry_px), float(atr_now or 0.0), float(atr_smooth or 0.0), float(qty))

        self.log.info(f"[OPEN] {symbol} {side} qty={qty_s} entry≈{fmt_price_str(self.client, symbol, entry_px)}")
        return True

    def close_position(self, symbol: str, side: str, qty: Optional[float] = None, reason: Optional[str] = None) -> bool:
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            self.log.error(f"[CLOSE] bad side {side}")
            return False

        if qty is None:
            qty = abs(self._fetch_position_qty(symbol))
        if qty <= 0:
            return True

        qty_s = fmt_qty_str(self.client, symbol, qty)
        side_ord = "SELL" if side == "LONG" else "BUY"

        def _place():
            return self.client.futures_create_order(
                symbol=symbol,
                side=side_ord,
                type="MARKET",
                quantity=qty_s,
                reduceOnly=True,
                newClientOrderId=self._gen_client_order_id(f"close-{symbol.lower()}"),
                recvWindow=10000,
            )

        order = self._with_retry(_place)
        if order:
            try:
                avg_px = _f(order.get("avgPrice", 0.0))
            except Exception:
                avg_px = 0.0
            if avg_px <= 0:
                try:
                    mp = self.client.futures_mark_price(symbol=symbol)
                    avg_px = _f(mp.get("markPrice", 0.0))
                except Exception:
                    avg_px = 0.0
            st = self.pos_registry.get(symbol, {})
            entry = float(st.get("entry_price") or self._fetch_entry_price(symbol))
            self._write_trade_stat(symbol, side, float(qty), float(entry), float(avg_px), reason or "MANUAL")
            self.log.info(f"[CLOSE] {symbol} {side} qty={qty_s} exit≈{avg_px}")
            if symbol in self.pos_registry:
                self.pos_registry.pop(symbol, None)
                self._persist_pos_registry()
            return True
        return False

    # ---------------- Хаускипинг ----------------
    def cleanup_orders(self, symbol: str, has_position: bool) -> None:
        try:
            open_orders = self.client.futures_get_open_orders(symbol=symbol, recvWindow=10000) or []
        except Exception as e:
            self.log.warning(f"[HK] {symbol} list open orders failed: {e}")
            return

        if not has_position:
            if open_orders:
                try:
                    self.client.futures_cancel_all_open_orders(symbol=symbol, recvWindow=10000)
                    self.log.info(f"[HK] {symbol} no position -> canceled {len(open_orders)}")
                except Exception as e:
                    self.log.warning(f"[HK] {symbol} cancel all warn: {e}")
            if symbol in self.pos_registry:
                self.pos_registry.pop(symbol, None)
                self._persist_pos_registry()
            return

        # при наличии позиции — оставляем ровно 1 SL/TP (reduce-only), дубликаты убираем
        keep_sl = None
        keep_tp = None
        garbage = []
        for o in open_orders:
            t = o.get("type", "")
            close_pos = bool(o.get("closePosition"))
            if t in ("STOP", "STOP_MARKET") and close_pos:
                keep_sl = max(keep_sl, o, key=lambda x: _f(x.get("updateTime", 0))) if keep_sl else o
            elif t in ("TAKE_PROFIT", "TAKE_PROFIT_MARKET") and close_pos:
                keep_tp = max(keep_tp, o, key=lambda x: _f(x.get("updateTime", 0))) if keep_tp else o
            else:
                garbage.append(o)

        # удаляем мусорные
        for g in garbage:
            try:
                self.client.futures_cancel_order(symbol=symbol, orderId=g["orderId"], recvWindow=10000)
            except Exception:
                pass

        # лишние дубликаты
        for o in open_orders:
            if keep_sl and o is not keep_sl and o.get("type") in ("STOP","STOP_MARKET") and o.get("closePosition"):
                try:
                    self.client.futures_cancel_order(symbol=symbol, orderId=o["orderId"], recvWindow=10000)
                except Exception:
                    pass
            if keep_tp and o is not keep_tp and o.get("type") in ("TAKE_PROFIT","TAKE_PROFIT_MARKET") and o.get("closePosition"):
                try:
                    self.client.futures_cancel_order(symbol=symbol, orderId=o["orderId"], recvWindow=10000)
                except Exception:
                    pass

        # если чего-то нет — поставим заново
        if not keep_sl or not keep_tp:
            try:
                pos = self.client.futures_position_information(symbol=symbol, recvWindow=10000) or []
                if not pos:
                    return
                p = pos[0]
                qty = abs(_f(p.get("positionAmt"), 0.0))
                if qty <= 0:
                    return
                side = "LONG" if _f(p.get("positionAmt"), 0.0) > 0 else "SHORT"
                entry = _f(p.get("entryPrice"), 0.0)
                self.set_sl_tp(symbol, side, entry, 0.0, 0.0, qty)
            except Exception as e:
                self.log.warning(f"[HK] {symbol} re-set exits warn: {e}")

        # синхроним registry IDs
        st = self.pos_registry.get(symbol) or {}
        st["sl_id"] = keep_sl.get("orderId") if keep_sl else None
        st["tp_id"] = keep_tp.get("orderId") if keep_tp else None
        self.pos_registry[symbol] = st
        self._persist_pos_registry()

        # reconcile: если SL/TP уже сработали — допишем сделку в статистику
        try:
            self._reconcile_symbol(symbol)
        except Exception as e:
            self.log.debug(f"[HK] {symbol} reconcile warn: {e}")

    # ---------------- Внутреннее: reconcile ----------------
    def _reconcile_symbol(self, symbol: str):
        """
        Сверка факта закрытия позиции по SL/TP и запись сделки в статистику,
        если выход случился не через close_position(), а ордеры reduce-only исполнились сами.
        """
        st = self.pos_registry.get(symbol)
        if not st:
            return

        try:
            pis = self.client.futures_position_information(symbol=symbol, recvWindow=10000) or []
        except Exception:
            pis = []
        pos_amt = 0.0
        if pis:
            try:
                pos_amt = float(pis[0].get("positionAmt") or 0.0)
            except Exception:
                pos_amt = 0.0

        # Если позиция ещё открыта — нечего сверять
        if abs(pos_amt) > 0:
            return

        entry_px = float(st.get("entry_price") or 0.0)
        side_in  = (st.get("side") or "").upper()
        sl_id = st.get("sl_id")
        tp_id = st.get("tp_id")

        qty_sum = 0.0
        pv_sum  = 0.0
        reason  = None

        def _pull(order_id):
            if not order_id:
                return None
            try:
                return self.client.futures_get_order(symbol=symbol, orderId=int(order_id), recvWindow=10000)
            except Exception:
                return None

        sl_od = _pull(sl_id)
        tp_od = _pull(tp_id)

        for od, rname in ((sl_od, "SL"), (tp_od, "TP")):
            if not od:
                continue
            status = str(od.get("status", "")).upper()
            try:
                q = float(od.get("executedQty") or 0.0)
                p = float(od.get("avgPrice") or 0.0)
            except Exception:
                q = 0.0
                p = 0.0
            if status in ("FILLED", "PARTIALLY_FILLED") and q > 0 and p > 0:
                qty_sum += q
                pv_sum  += p * q
                reason = reason or rname

        if qty_sum <= 0:
            try:
                mp = self.client.futures_mark_price(symbol=symbol)
                exit_px = float(mp.get("markPrice") or 0.0)
            except Exception:
                exit_px = entry_px
            if exit_px <= 0:
                return
            self._write_trade_stat(symbol, side_in, abs(float(st.get("qty") or 0.0)), entry_px, exit_px, reason or "CLOSE")
        else:
            exit_px = pv_sum / qty_sum
            self._write_trade_stat(symbol, side_in, float(qty_sum), entry_px, exit_px, reason or "CLOSE")

        try:
            self.pos_registry.pop(symbol, None)
            self._persist_pos_registry()
        except Exception:
            pass
        self.log.info(f"[RECONCILE] {symbol} {side_in} closed by {reason or 'CLOSE'}")

    # ---------------- SL/TP ----------------
    def set_sl_tp(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        atr_now: float,
        atr_smooth: float,
        qty: float,
    ):
        side = side.upper()
        sl_working = getattr(cfg, "SL_WORKING_TYPE", "MARK_PRICE")
        tp_working = getattr(cfg, "TP_WORKING_TYPE", "CONTRACT_PRICE")
        use_atr = bool(getattr(cfg, "USE_ATR_EXITS", True))
        sl_pct = float(getattr(cfg, "SL_PCT", 0.006))
        tp_pct = float(getattr(cfg, "TP_PCT", 0.005))
        atr_mult_sl = float(getattr(cfg, "ATR_MULT_SL", getattr(cfg, "ATR_SL_MULT", 1.8)))
        atr_mult_tp = float(getattr(cfg, "ATR_MULT_TP", getattr(cfg, "ATR_TP_MULT", 2.5)))

        if use_atr and (atr_now or atr_smooth):
            atr_val = float(atr_smooth or atr_now)
            if side == "LONG":
                sl = entry_price - atr_val * atr_mult_sl
                tp = entry_price + atr_val * atr_mult_tp
            else:
                sl = entry_price + atr_val * atr_mult_sl
                tp = entry_price - atr_val * atr_mult_tp
        else:
            if side == "LONG":
                sl = entry_price * (1.0 - sl_pct)
                tp = entry_price * (1.0 + tp_pct)
            else:
                sl = entry_price * (1.0 + sl_pct)
                tp = entry_price * (1.0 - tp_pct)

        try:
            sl = quantize_price(self.client, symbol, sl, mode="round")
            tp = quantize_price(self.client, symbol, tp, mode="round")
        except Exception as e:
            self.log.debug(f"[EXITS] {symbol} price quant warn: {e}")

        side_exit = "SELL" if side == "LONG" else "BUY"
        qty_s = fmt_qty_str(self.client, symbol, qty)
        sl_px_s = fmt_price_str(self.client, symbol, sl)
        tp_px_s = fmt_price_str(self.client, symbol, tp)

        try:
            sl_order = self.client.futures_create_order(
                symbol=symbol, side=side_exit, type="STOP_MARKET",
                stopPrice=sl_px_s, closePosition=True, workingType=sl_working, recvWindow=10000,
                newClientOrderId=self._gen_client_order_id(f"sl-{symbol.lower()}"),
            )
            tp_order = self.client.futures_create_order(
                symbol=symbol, side=side_exit, type="TAKE_PROFIT_MARKET",
                stopPrice=tp_px_s, closePosition=True, workingType=tp_working, recvWindow=10000,
                newClientOrderId=self._gen_client_order_id(f"tp-{symbol.lower()}"),
            )
        except Exception as e:
            self.log.error(f"[EXITS] {symbol} place SL/TP error: {e}")
            return

        st = self.pos_registry.get(symbol, {}) or {}
        st["sl_id"] = sl_order.get("orderId") if sl_order else None
        st["tp_id"] = tp_order.get("orderId") if tp_order else None
        self.pos_registry[symbol] = st
        self._persist_pos_registry()
        self.log.info(f"[EXITS] {symbol} SL@{sl_px_s} id={st['sl_id']} | TP@{tp_px_s} id={st['tp_id']}")

    # ---------------- helpers ----------------
    def _gen_client_order_id(self, prefix: str) -> str:
        return f"{prefix}-{int(time.time()*1000)%10000000}-{random.randint(0,999)}"

    def _fetch_entry_price(self, symbol: str) -> float:
        try:
            pis = self.client.futures_position_information(symbol=symbol, recvWindow=10000) or []
            if not pis:
                return 0.0
            return _f(pis[0].get("entryPrice"), 0.0)
        except Exception:
            return 0.0

    def _fetch_position_qty(self, symbol: str) -> float:
        try:
            pis = self.client.futures_position_information(symbol=symbol, recvWindow=10000) or []
            if not pis:
                return 0.0
            return abs(_f(pis[0].get("positionAmt"), 0.0))
        except Exception:
            return 0.0

    def _with_retry(self, fn, retries: int = 5, base_sleep: float = 0.25):
        for i in range(retries):
            try:
                return fn()
            except BinanceAPIException as e:
                if e.code in (429, 418, -1003, -1021):
                    time.sleep(base_sleep * (2 ** i))
                    continue
                raise
            except Exception:
                time.sleep(base_sleep * (2 ** i))
        raise

    def _write_trade_stat(self, symbol: str, side: str, qty: float, entry: float, exit: float, reason: str):
        pnl = (exit - entry) * qty if (side.upper() == "LONG") else (entry - exit) * qty
        with open(self._stats_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([_now_iso(), symbol, side.upper(), float(qty), float(entry), float(exit), reason, float(pnl)])

    # --- публичный API (опционально) ---
    def record_trade(self, symbol: str, side: str, qty: float,
                     entry_price: float, exit_price: float,
                     fees_usd: Optional[float] = None, reason: Optional[str] = ""):
        if not self.enabled:
            return
        qty = abs(float(qty))
        entry_price = float(entry_price)
        exit_price = float(exit_price)
        notional = max(qty * ((entry_price + exit_price) / 2.0), 0.0)
        if fees_usd is None:
            fees_usd = notional * self.fee_taker
        pnl = (exit_price - entry_price) * qty if (side.upper() == "LONG") else (entry_price - exit_price) * qty
        with open(self._stats_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([_now_iso(), symbol, side.upper(), qty, entry_price, exit_price, reason or "", pnl - fees_usd])
