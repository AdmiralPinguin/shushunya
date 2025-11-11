from dotenv import load_dotenv

load_dotenv()

import os
import time
from pathlib import Path

import pandas as pd
from binance.client import Client

from core.quant import parse_futures_exchange_info, apply_precision

USDT_PER_TRADE = 10.0
LEVERAGE = 4
BASE_MIN_PCT = 0.01
THRESH = 3.0

DATA_DIR = Path("/media/acab/LMS/Shushunya/Sharomyga/data/processed")

_client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
_fmap = None
_state = {}  # symbol -> {entry, tp, sl, side}


def _dynamic_min_pct(symbol: str, k: float = 1.3, window: int = 200) -> float:
    """
    Оценка минимального хода из оффлайн-датасета под новую разметку.
    Берём средний диапазон m15 и нормируем на цену.
    """
    try:
        path = DATA_DIR / f"{symbol}.csv"
        if not path.exists():
            return BASE_MIN_PCT

        df = pd.read_csv(path)
        if "m15_high" not in df.columns or "m15_low" not in df.columns or "m15_close" not in df.columns:
            return BASE_MIN_PCT

        df = df.sort_values("time").tail(window)
        if len(df) < 20:
            return BASE_MIN_PCT

        rng = (df["m15_high"] - df["m15_low"]).mean()
        last_price = df["m15_close"].iloc[-1]
        if last_price <= 0:
            return BASE_MIN_PCT

        dyn = float(rng / last_price) * float(k)
        return max(dyn, BASE_MIN_PCT)
    except Exception as e:
        print(f"[{symbol}] dyn_pct error: {e}")
        return BASE_MIN_PCT


def _ensure_filters(sym: str):
    global _fmap
    if _fmap is None or sym not in _fmap:
        info = _client.futures_exchange_info()
        _fmap = parse_futures_exchange_info(info)


def _has_position(sym: str) -> bool:
    for p in _client.futures_position_information():
        if p["symbol"] == sym and abs(float(p["positionAmt"])) > 0:
            return True
    return False


def _decide(sym: str, up: float, down: float, price: float):
    up = max(up, 0.0)
    dn = max(down, 0.0)
    dyn = _dynamic_min_pct(sym)

    if up >= THRESH * dn and (up / price) >= dyn:
        return "LONG"
    if dn >= THRESH * up and (dn / price) >= dyn:
        return "SHORT"
    return None


def _cancel_all(sym: str):
    try:
        _client.futures_cancel_all_open_orders(symbol=sym)
    except Exception as e:
        print(f"[{sym}] cancel_all error: {e}")


def _place(sym: str, side: str, price: float, up: float, down: float):
    _ensure_filters(sym)
    sf = _fmap.get(sym)
    if sf is None:
        print(f"[{sym}] no filters, HOLD")
        return

    qty_usdt = USDT_PER_TRADE * LEVERAGE
    qty_raw = qty_usdt / price if price > 0 else 0
    _, qty = apply_precision(sf, price, qty_raw, notional_guard=True)
    qty = float(qty)

    if qty <= 0:
        print(f"[{sym}] HOLD tiny qty")
        return

    entry_side = "BUY" if side == "LONG" else "SELL"
    exit_side = "SELL" if side == "LONG" else "BUY"

    try:
        _client.futures_create_order(
            symbol=sym,
            side=entry_side,
            type="MARKET",
            quantity=qty,
        )
        print(f"[{sym}] ENTER {side} q={qty}")
    except Exception as e:
        print(f"[{sym}] entry fail: {e}")
        return

    time.sleep(0.4)
    price = float(_client.futures_mark_price(symbol=sym)["markPrice"])

    tp = price + 0.7 * up if side == "LONG" else price - 0.7 * down
    sl = price - 0.4 * up if side == "LONG" else price + 0.4 * down

    tp_q, _ = apply_precision(sf, tp, qty, notional_guard=False)
    sl_q, _ = apply_precision(sf, sl, qty, notional_guard=False)

    try:
        _client.futures_create_order(
            symbol=sym,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=float(sl_q),
            closePosition=True,
            workingType="MARK_PRICE",
        )
        _client.futures_create_order(
            symbol=sym,
            side=exit_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=float(tp_q),
            closePosition=True,
            workingType="MARK_PRICE",
        )
        print(f"[{sym}] TP={float(tp_q):.6f} SL={float(sl_q):.6f}")
    except Exception as e:
        print(f"[{sym}] bracket fail: {e}")

    _state[sym] = {"entry": price, "tp": float(tp_q), "sl": float(sl_q), "side": side}


def _trail(sym: str, price: float, up: float, down: float):
    if sym not in _state:
        print(f"[{sym}] HOLD (missing state)")
        return

    st = _state[sym]
    side = st["side"]

    tp = price + 0.7 * up if side == "LONG" else price - 0.7 * down
    sl = price - 0.4 * up if side == "LONG" else price + 0.4 * down

    if abs(price - st["entry"]) >= 0.4 * abs(st["tp"] - st["entry"]):
        sl = st["entry"] * (1.001 if side == "LONG" else 0.999)

    if abs(tp - st["entry"]) > abs(st["tp"] - st["entry"]):
        st["tp"] = tp
    if (side == "LONG" and sl > st["sl"]) or (side == "SHORT" and sl < st["sl"]):
        st["sl"] = sl

    _cancel_all(sym)

    exit_side = "SELL" if side == "LONG" else "BUY"
    sf = _fmap.get(sym)
    if sf is None:
        print(f"[{sym}] no filters on trail")
        return

    qty = 0.0
    for p in _client.futures_position_information():
        if p["symbol"] == sym:
            qty = abs(float(p["positionAmt"]))
            break

    if qty <= 0:
        print(f"[{sym}] no qty on trail")
        return

    tp_q, _ = apply_precision(sf, st["tp"], qty, notional_guard=False)
    sl_q, _ = apply_precision(sf, st["sl"], qty, notional_guard=False)

    try:
        _client.futures_create_order(
            symbol=sym,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=float(sl_q),
            closePosition=True,
            workingType="MARK_PRICE",
        )
        _client.futures_create_order(
            symbol=sym,
            side=exit_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=float(tp_q),
            closePosition=True,
            workingType="MARK_PRICE",
        )
        print(f"[{sym}] TRAIL SET tp={float(tp_q):.6f} sl={float(sl_q):.6f}")
    except Exception as e:
        print(f"[{sym}] TRAIL ERR: {e}")


def process_signal(sym: str, price: float, up: float, down: float):
    if _has_position(sym):
        _trail(sym, price, up, down)
        print(f"[{sym}] HOLD (trail)")
        return

    side = _decide(sym, up, down, price)
    if side is None:
        print(f"[{sym}] HOLD (no signal)")
        return

    _place(sym, side, price, up, down)
