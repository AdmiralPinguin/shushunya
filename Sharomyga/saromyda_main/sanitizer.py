# trade_bot_fixed/sanitizer.py
import time
from . import config as cfg
from .exchange_utils import quantize_price
from .futures_executor import FuturesExecutor

def _f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

class Sanitizer:
    def __init__(self, client, executor: FuturesExecutor, logger):
        self.client = client
        self.execu = executor
        self.log = logger

    def _calc_targets(self, symbol: str, side: str, entry_price: float, atr_now: float, atr_smooth: float):
        sl_pct = float(getattr(cfg, "SL_PCT", 0.006))
        tp_pct = float(getattr(cfg, "TP_PCT", 0.005))
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
            self.log.debug(f"[SAN] {symbol} price quant warn: {e}")
        return float(sl), float(tp)

    def sanitize_symbol(self, symbol: str, wallet_usdt: float, atr_now: float, atr_smooth: float):
        pis = self.client.futures_position_information(symbol=symbol, recvWindow=10000) or []
        if not pis:
            try:
                self.execu._reconcile_symbol(symbol)
            except Exception:
                pass
            self.execu.pos_registry.pop(symbol, None)
            self.execu._persist_pos_registry()
            return
        p = pis[0]
        pos_amt = _f(p.get("positionAmt"), 0.0)
        if abs(pos_amt) == 0.0:
            try:
                self.execu._reconcile_symbol(symbol)
            except Exception:
                pass
            self.execu.pos_registry.pop(symbol, None)
            self.execu._persist_pos_registry()
            return

        side = "LONG" if pos_amt > 0 else "SHORT"
        entry = _f(p.get("entryPrice", 0.0))

        # реестр — жёстко переписываем фактами с биржи
        st = self.execu.pos_registry.get(symbol) or {}
        st["side"] = side
        st["entry_price"] = float(entry)
        st["qty"] = abs(float(pos_amt))
        st["sl_id"] = st.get("sl_id")
        st["tp_id"] = st.get("tp_id")
        st["opened_ms"] = st.get("opened_ms") or int(time.time() * 1000)
        self.execu.pos_registry[symbol] = st

        # открытые ордера
        open_orders = self.client.futures_get_open_orders(symbol=symbol, recvWindow=10000) or []
        sl_orders = [o for o in open_orders if o.get("type") in ("STOP","STOP_MARKET") and o.get("closePosition")]
        tp_orders = [o for o in open_orders if o.get("type") in ("TAKE_PROFIT","TAKE_PROFIT_MARKET") and o.get("closePosition")]

        need_set = (len(sl_orders) != 1) or (len(tp_orders) != 1)
        if need_set:
            try:
                self.client.futures_cancel_all_open_orders(symbol=symbol, recvWindow=10000)
            except Exception as e:
                self.log.debug(f"[SAN] {symbol} cancel all warn: {e}")
            self.execu.set_sl_tp(symbol, side, float(entry), float(atr_now or 0.0), float(atr_smooth or 0.0), float(abs(pos_amt)))
            st = self.execu.pos_registry.get(symbol, {}) or {}
        else:
            st["sl_id"] = sl_orders[0].get("orderId") if sl_orders else None
            st["tp_id"] = tp_orders[0].get("orderId") if tp_orders else None

        self.execu.pos_registry[symbol] = st
        self.execu._persist_pos_registry()

        self.log.info(f"[SNAP] {symbol} {side} qty={st['qty']} @ {entry}; EXITS: SL={st.get('sl_id')} TP={st.get('tp_id')}")
