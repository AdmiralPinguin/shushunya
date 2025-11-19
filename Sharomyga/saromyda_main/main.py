# -*- coding: utf-8 -*-
# trade_bot_fixed/main.py
from __future__ import annotations

import os
import time
import inspect
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client

from . import config as cfg
from .voting import compute_votes
from .decision_router import plan_action, explain_decision
from .futures_executor import FuturesExecutor
from .logger import get_logger
from .stats import StatsTracker
from .sanitizer import Sanitizer

load_dotenv()

_last_votes = {}   # symbol -> last votes

def _f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def fetch_klines(client: Client, symbol: str, interval: str, limit: int):
    kb = client.futures_klines(symbol=symbol, interval=interval, limit=limit, recvWindow=10000)
    cols = ["open_time","open","high","low","close","volume","close_time","quote_asset_volume",
            "num_trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(kb, columns=cols)
    for c in ("open","high","low","close","volume"):
        df[c] = df[c].astype(float)
    return df

def apply_leverage_isolated(execu: FuturesExecutor, symbols):
    for s in symbols:
        try:
            execu.ensure_symbol_mode(s, leverage=int(getattr(cfg, "LEVERAGE", 2)), margin_type="ISOLATED")
        except Exception:
            pass

def futures_account_info(client: Client):
    info = client.futures_account(recvWindow=10000)
    wallet = float(info.get("totalWalletBalance", 0.0))
    avail  = float(info.get("availableBalance", 0.0))
    im     = float(info.get("totalInitialMargin", 0.0))
    mm     = float(info.get("totalMaintMargin", 0.0))
    upnl   = float(info.get("totalUnrealizedProfit", 0.0))
    return wallet, avail, im, mm, upnl

def futures_wallet_balance(client: Client) -> float:
    acc = client.futures_account_balance(recvWindow=10000)
    for a in acc:
        if a["asset"] == "USTE":
            pass
    for a in acc:
        if a["asset"] == "USDT":
            return float(a["balance"])
    return 0.0

def positions_map(client: Client):
    pos = {}
    infos = client.futures_position_information(recvWindow=10000)
    for p in infos:
        sym = p["symbol"]
        amt = float(p["positionAmt"])
        if amt > 0:
            pos[sym] = ("LONG",  amt, float(p.get("entryPrice", 0.0) or 0.0))
        elif amt < 0:
            pos[sym] = ("SHORT", -amt, float(p.get("entryPrice", 0.0) or 0.0))
    return pos

def open_orders_count(client: Client) -> int:
    try:
        return len(client.futures_get_open_orders(recvWindow=10000))
    except Exception:
        return 0

def _time_sync_log(log, client: Client):
    try:
        server_time = client.futures_time()
        drift = int(server_time["serverTime"]) - int(time.time() * 1000)
        log.info(f"[TIME] drift(ms)={drift}")
    except Exception:
        pass

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([
        (h - l).abs(),
        (h - c.shift(1)).abs(),
        (l - c.shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def votes_arrow(prev: float, curr: float) -> str:
    return "↑" if curr > prev else ("↓" if curr < prev else "→")

# --- Совместимость со старым кодом логов ---
def _arrow(curr: float, prev):
    if prev is None:
        return " "
    return "↑" if curr > prev else ("↓" if curr < prev else "→")

def futures_account_debug(client: Client):
    return futures_account_info(client)

def _fmt_votes_classic(symbol: str, v, prev_v):
    """
    Returns a simplified string representation of votes for long/short decisions.
    This version omits arrows and tick marks, showing only the raw vote values.

    Example:
      L[LE9.12 LX2.10] S[SE1.98 SX3.45]
    """
    L = f"L[LE{v.long_entry:.2f} LX{v.long_exit:.2f}]"
    S = f"S[SE{v.short_entry:.2f} SX{v.short_exit:.2f}]"
    return f"{L} {S}"

def _pos_state_str(action: str, has_long: bool, has_short: bool) -> str:
    """
    Determine a concise position state string to append to log lines.
    Returns posL:✓, posS:✓, or an empty string.
    """
    if action in ("OPEN_LONG", "FLIP_TO_LONG"):
        return "posL:✓"
    if action in ("OPEN_SHORT", "FLIP_TO_SHORT"):
        return "posS:✓"
    if action in ("CLOSE_LONG", "CLOSE_SHORT", "CLOSE_BY_RETRACE"):
        return ""
    if has_long:
        return "posL:✓"
    if has_short:
        return "posS:✓"
    return ""

# --- Универсальный вызов (не меняет логику, лишь совместимость) ---
def _call_best(fn, *candidates):
    try:
        sig = inspect.signature(fn)
        params = [p for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        n = len(params)
    except Exception:
        n = len(candidates)
    return fn(*candidates[:n])

# ---------------- main loop ----------------
def main_loop():
    log = get_logger("trade-bot")

    api_key = os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY")
    api_sec = os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET")
    if not api_key or not api_sec:
        log.error("No API keys in env (BINANCE_API_KEY / BINANCE_API_SECRET).")
        return

    client = Client(api_key, api_sec, requests_params={"timeout": 10})
    execu  = FuturesExecutor(client, log)
    stats  = StatsTracker(base_dir=cfg.STATS_DIR, fee_taker=cfg.FEE_TAKER,
                          enabled=cfg.STATS_ENABLED, logger=log)
    san    = Sanitizer(client, execu, log)

    apply_leverage_isolated(execu, cfg.SYMBOLS)

    while True:
        try:
            if getattr(cfg, "TIME_SYNC_EACH_CYCLE", True):
                _time_sync_log(log, client)

            log.info("=== Новый торговый цикл ===")

            try:
                wallet, avail, im, mm, upnl = futures_account_info(client)
                log.info(f"DEBUG Futures USD-M wallet={wallet:.2f} avail={avail:.2f} IM={im:.2f} MM={mm:.2f} UPNL={upnl:.2f}")
            except Exception as e:
                log.warning(f"acct info warn: {e}")
                wallet = futures_wallet_balance(client)
                avail = wallet

            pos_map = positions_map(client)
            open_ord = open_orders_count(client)
            quota = max(int(getattr(cfg, "MAX_OPEN_POS", 6)) - len(pos_map), 0)
            log.info(f"USDT={wallet:.2f} | open_pos={len(pos_map)} | open_ord={open_ord} | quota={quota}")

            boosted_syms, full_syms, half_syms, flip_syms, hold_syms = [], [], [], [], []
            _pos_state = {}  # symbol -> {opened_ts, mfe_pct}

            for symbol in cfg.SYMBOLS:
                try:
                    # сверка статы если закрылась по SL/TP
                    try:
                        execu._reconcile_symbol(symbol)
                    except Exception:
                        pass

                    df = fetch_klines(client, symbol, cfg.TIMEFRAME, int(getattr(cfg, "LIMIT", 200)))
                    if len(df) < int(getattr(cfg, "LIMIT", 200)) * 0.9:
                        log.info(f"{symbol} | мало истории, пропуск")
                        continue

                    has_long = symbol in pos_map and pos_map[symbol][0] == "LONG"
                    has_short = symbol in pos_map and pos_map[symbol][0] == "SHORT"
                    qty_open = pos_map[symbol][1] if symbol in pos_map else 0.0
                    entry_price = pos_map[symbol][2] if symbol in pos_map else 0.0

                    try:
                        mp = client.futures_mark_price(symbol=symbol)
                        mark_price = _f(mp.get("markPrice"), df["close"].iloc[-1])
                    except Exception:
                        mark_price = float(df["close"].iloc[-1])

                    atr_now = float(atr(df, int(getattr(cfg,"ATR_PERIOD",14))).iloc[-1])
                    atr_smooth = float(pd.Series([atr_now]).ewm(span=96, adjust=False).mean().iloc[-1])

                    if has_long or has_short:
                        stats.observe(symbol, qty_open if has_long else -qty_open, entry_price, mark_price)

                    v = compute_votes(df, cfg)
                    pretty = _fmt_votes_classic(symbol, v, _last_votes.get(symbol))
                    _last_votes[symbol] = v

                    try:
                        action = _call_best(plan_action, v, has_long, has_short, cfg)
                    except Exception as e:
                        log.warning(f"{symbol} | plan_action error: {e}")
                        action = "HOLD"

                    try:
                        reason = _call_best(explain_decision, v, has_long, has_short, cfg)
                    except Exception:
                        reason = None

                    # Determine concise position string and calculate dynamic sizing factor and soft/hard flags
                    pos_str = _pos_state_str(action, has_long, has_short)

                    # Soft and hard threshold checks for half positions
                    long_soft = v.long_entry >= float(getattr(cfg, "LONG_ENTRY_SOFT_BUY", 8.0))
                    long_hard = v.long_entry >= float(getattr(cfg, "LONG_ENTRY_MIN_BUY", 9.0))
                    short_soft = v.short_entry >= float(getattr(cfg, "SHORT_ENTRY_SOFT_SELL", 8.0))
                    short_hard = v.short_entry >= float(getattr(cfg, "SHORT_ENTRY_MIN_SELL", 9.0))

                    # Dynamic position sizing factor based on ATR/price (if enabled)
                    dyn_frac = 1.0
                    if getattr(cfg, "ATR_DYNAMIC_SIZE_ENABLED", False):
                        try:
                            atr_pct = atr_now / max(mark_price, 1e-12)
                            base_risk_pct = float(getattr(cfg, "ATR_BASE_RISK_PCT", 0.01))
                            dyn_val = base_risk_pct / max(atr_pct, 1e-12)
                            min_frac = float(getattr(cfg, "ATR_MIN_FRACTION", 0.2))
                            max_frac = float(getattr(cfg, "ATR_MAX_FRACTION", 2.0))
                            dyn_frac = max(min(dyn_val, max_frac), min_frac)
                        except Exception:
                            dyn_frac = 1.0

                    printed = False

                    # === FLIP ===
                    if action == "FLIP_TO_SHORT" and has_long:
                        try:
                            execu.close_position(symbol, side="LONG", qty=qty_open)
                        except Exception:
                            pass
                        ok = execu.open_position(symbol, side="SHORT", wallet_usdt=wallet, mark_price=mark_price,
                                                 atr_now=atr_now, atr_smooth=atr_smooth, fraction=1.0 * dyn_frac)
                        if ok:
                            _pos_state[symbol] = {"opened_ts": time.time(), "mfe_pct": 0.0}
                            flip_syms.append(symbol)
                            log.info(f"{symbol} | FLIP_TO_SHORT | {pretty}" + (f" | {pos_str}" if pos_str else ""))
                            printed = True

                    elif action == "FLIP_TO_LONG" and has_short:
                        try:
                            execu.close_position(symbol, side="SHORT", qty=qty_open)
                        except Exception:
                            pass
                        ok = execu.open_position(symbol, side="LONG", wallet_usdt=wallet, mark_price=mark_price,
                                                 atr_now=atr_now, atr_smooth=atr_smooth, fraction=1.0 * dyn_frac)
                        if ok:
                            _pos_state[symbol] = {"opened_ts": time.time(), "mfe_pct": 0.0}
                            flip_syms.append(symbol)
                            log.info(f"{symbol} | FLIP_TO_LONG | {pretty}" + (f" | {pos_str}" if pos_str else ""))
                            printed = True

                    # === CLOSE ===
                    elif action == "CLOSE_LONG" and has_long:
                        execu.close_position(symbol, side="LONG", qty=qty_open)
                        log.info(f"{symbol} | CLOSE_LONG | {pretty}" + (f" | {pos_str}" if pos_str else ""))
                        printed = True

                    elif action == "CLOSE_SHORT" and has_short:
                        execu.close_position(symbol, side="SHORT", qty=qty_open)
                        log.info(f"{symbol} | CLOSE_SHORT | {pretty}" + (f" | {pos_str}" if pos_str else ""))
                        printed = True

                    # === OPEN ===
                    elif action == "OPEN_LONG" and (not has_long and not has_short) and quota > 0:
                        base_frac = 1.5 if v.long_entry >= float(getattr(cfg,"BOOST_THRESHOLD",8.0)) else 1.0
                        final_frac = base_frac * dyn_frac
                        ok = execu.open_position(symbol, side="LONG", wallet_usdt=wallet, mark_price=mark_price,
                                                 atr_now=atr_now, atr_smooth=atr_smooth, fraction=final_frac)
                        if ok:
                            _pos_state[symbol] = {"opened_ts": time.time(), "mfe_pct": 0.0}
                            (boosted_syms if base_frac > 1.0 else full_syms).append(symbol)
                            log.info(f"{symbol} | OPEN_LONG{' BOOST' if base_frac>1.0 else ''} | {pretty}" + (f" | {pos_str}" if pos_str else ""))
                            printed = True
                            quota -= 1

                    elif action == "OPEN_SHORT" and (not has_long and not has_short) and quota > 0 and bool(getattr(cfg,"ALLOW_SHORTS",True)):
                        base_frac = 1.5 if v.short_entry >= float(getattr(cfg,"BOOST_THRESHOLD",8.0)) else 1.0
                        final_frac = base_frac * dyn_frac
                        ok = execu.open_position(symbol, side="SHORT", wallet_usdt=wallet, mark_price=mark_price,
                                                 atr_now=atr_now, atr_smooth=atr_smooth, fraction=final_frac)
                        if ok:
                            _pos_state[symbol] = {"opened_ts": time.time(), "mfe_pct": 0.0}
                            (boosted_syms if base_frac > 1.0 else full_syms).append(symbol)
                            log.info(f"{symbol} | OPEN_SHORT{' BOOST' if base_frac>1.0 else ''} | {pretty}" + (f" | {pos_str}" if pos_str else ""))
                            printed = True
                            quota -= 1

                    # === SOFT HALF open for HOLD action ===
                    elif action == "HOLD" and (not has_long and not has_short) and quota > 0 and bool(getattr(cfg, "SOFT_HALF_ENABLED", False)):
                        opened_soft = False
                        if long_soft and not long_hard:
                            base_frac_soft = 0.5
                            final_frac_soft = base_frac_soft * dyn_frac
                            ok = execu.open_position(symbol, side="LONG", wallet_usdt=wallet, mark_price=mark_price,
                                                     atr_now=atr_now, atr_smooth=atr_smooth, fraction=final_frac_soft)
                            if ok:
                                _pos_state[symbol] = {"opened_ts": time.time(), "mfe_pct": 0.0}
                                half_syms.append(symbol)
                                # explicit pos string since action is HOLD
                                log.info(f"{symbol} | OPEN_LONG HALF | {pretty}" + " | posL:✓")
                                printed = True
                                quota -= 1
                                opened_soft = True
                        elif short_soft and not short_hard and bool(getattr(cfg, "ALLOW_SHORTS", True)):
                            base_frac_soft = 0.5
                            final_frac_soft = base_frac_soft * dyn_frac
                            ok = execu.open_position(symbol, side="SHORT", wallet_usdt=wallet, mark_price=mark_price,
                                                     atr_now=atr_now, atr_smooth=atr_smooth, fraction=final_frac_soft)
                            if ok:
                                _pos_state[symbol] = {"opened_ts": time.time(), "mfe_pct": 0.0}
                                half_syms.append(symbol)
                                log.info(f"{symbol} | OPEN_SHORT HALF | {pretty}" + " | posS:✓")
                                printed = True
                                quota -= 1
                                opened_soft = True

                    # === RETRACE TRAIL ===
                    if (has_long or has_short):
                        try:
                            side_pos = "LONG" if has_long else "SHORT"
                            qty_abs = float(abs(qty_open))
                            if qty_abs > 0:
                                if has_long:
                                    profit_usd = (mark_price - entry_price) * qty_abs
                                else:
                                    profit_usd = (entry_price - mark_price) * qty_abs
                                notional = max(mark_price * qty_abs, 1e-12)
                                profit_pct = profit_usd / notional

                                st = _pos_state.get(symbol) or {"opened_ts": time.time(), "mfe_pct": 0.0}
                                if profit_pct > st["mfe_pct"]:
                                    st["mfe_pct"] = profit_pct
                                _pos_state[symbol] = st

                                drop = st["mfe_pct"] - profit_pct
                                min_mfe = float(getattr(cfg, "RETRACE_MIN_MFE_PCT", 0.005))
                                drop_pct = float(getattr(cfg, "RETRACE_DROP_PCT", 0.002))
                                if st["mfe_pct"] >= min_mfe and drop >= drop_pct and profit_pct > 0:
                                    execu.close_position(symbol, side=side_pos, qty=qty_abs, reason="RETRACE")
                                    log.info(f"{symbol} | CLOSE_BY_RETRACE | {pretty}")
                                    try:
                                        stats.tag_close_reason(symbol, "RETRACE")
                                    except Exception:
                                        pass
                                    printed = True
                        except Exception as e:
                            pass

                    # санитар
                    try:
                        san.sanitize_symbol(symbol, wallet_usdt=wallet, atr_now=atr_now, atr_smooth=atr_smooth)
                    except Exception as e:
                        pass

                    if not printed:
                        hold_syms.append(symbol)
                        log.info(f"{symbol} | HOLD | {pretty}" + (f" | {pos_str}" if pos_str else ""))

                except Exception as e:
                    log.warning(f"{symbol} | symbol loop warn: {e}")

            # сводка + статистика
            try:
                # сначала выводим текущие открытые позиции, включая PnL
                current_positions = positions_map(client)
                if current_positions:
                    for _sym, (_side, _qty, _entry) in current_positions.items():
                        try:
                            mp_cur = client.futures_mark_price(symbol=_sym)
                            _mark = _f(mp_cur.get("markPrice"), _entry)
                        except Exception:
                            _mark = float(_entry or 0.0)
                        _pnl = (_mark - _entry) * _qty if _side == "LONG" else (_entry - _mark) * _qty
                        log.info(f"{_sym} | {_side} | qty={_qty:.4f} | entry={_entry:.4f} | mark={_mark:.4f} | pnl={_pnl:.4f}")
                log.info(f"ИТОГО: FULL={len(full_syms)} | HALF={len(half_syms)} | BOOSTED={len(boosted_syms)} | FLIP={len(flip_syms)} | HOLD={len(hold_syms)}")
                stats.recompute_and_persist()
                for _line in stats.mini_summary().split("\n"):
                    log.info(_line)
            except Exception as e:
                log.warning(f"stats warn: {e}")

            # сон
            sleep_sec = int(getattr(cfg, "CYCLE_SLEEP_SEC", 300))
            if getattr(cfg, "FAST_MODE_ENABLED", False) and _pos_state:
                now = time.time()
                any_fast1 = any((now - st.get("opened_ts", 0)) < int(getattr(cfg, "FAST1_DURATION_SEC", 90)) for st in _pos_state.values())
                any_fast2 = any((now - st.get("opened_ts", 0)) < int(getattr(cfg, "FAST2_DURATION_SEC", 600)) for st in _pos_state.values())
                if any_fast1:
                    sleep_sec = min(sleep_sec, int(getattr(cfg, "FAST1_SEC", 5)))
                elif any_fast2:
                    sleep_sec = min(sleep_sec, int(getattr(cfg, "FAST2_SEC", 20)))
            log.info(f"Ждём {sleep_sec} сек...")
            time.sleep(sleep_sec)

        except Exception as e:
            log.error(f"Unhandled exception in main loop: {e}", exc_info=True)
            time.sleep(5)
            continue

if __name__ == "__main__":
    main_loop()
