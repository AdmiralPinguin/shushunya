from dotenv import load_dotenv; load_dotenv()
import os, time
import pandas as pd
from binance.client import Client
from core.quant import parse_futures_exchange_info, apply_precision

USDT_PER_TRADE = 10.0
LEVERAGE = 4
BASE_MIN_PCT = 0.01
THRESH = 3.0

_client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
_fmap = None
_state = {}  # symbol -> {entry, tp, sl, side}


# === Dynamic volatility threshold (ATR-like) ===
def _dynamic_min_pct(symbol, data_path="/media/acab/LMS/Shushunya/SharomygaReforged/data/processed/all_pairs_merged.csv", k=1.3, window=50):
    try:
        df = pd.read_csv(data_path)
        df = df[df["symbol"] == symbol].sort_values("time").tail(window)
        if len(df) < 5:
            return BASE_MIN_PCT
        rng = (df["m15_high"] - df["m15_low"]).mean()
        last_price = df["m15_close"].iloc[-1]
        dyn = (rng / last_price) * k
        return max(dyn, BASE_MIN_PCT)
    except Exception as e:
        print(f"[{symbol}] dyn_pct error: {e}")
        return BASE_MIN_PCT


def _ensure_filters(sym):
    global _fmap
    if _fmap is None or sym not in _fmap:
        _fmap = parse_futures_exchange_info(_client.futures_exchange_info())


def _has_position(sym):
    for p in _client.futures_position_information():
        if p["symbol"] == sym and abs(float(p["positionAmt"])) > 0:
            return True
    return False


def _decide(sym, up, down, price):
    up = max(up, 0); dn = max(down, 0)
    dyn = _dynamic_min_pct(sym)
    if up >= THRESH * dn and (up / price) >= dyn:
        return "LONG"
    if dn >= THRESH * up and (dn / price) >= dyn:
        return "SHORT"
    return None


def _cancel_all(sym):
    try:
        _client.futures_cancel_all_open_orders(symbol=sym)
    except:
        pass


def _place(sym, side, price, up, down):
    _ensure_filters(sym)
    sf = _fmap[sym]

    qty = float(apply_precision(sf, price, USDT_PER_TRADE / price * LEVERAGE, True)[1])
    if qty <= 0:
        print(f"[{sym}] HOLD tiny qty")
        return

    entry_side = "BUY" if side == "LONG" else "SELL"
    exit_side = "SELL" if side == "LONG" else "BUY"

    try:
        _client.futures_create_order(symbol=sym, side=entry_side, type="MARKET", quantity=qty)
        print(f"[{sym}] ENTER {side} q={qty}")
    except Exception as e:
        print(f"[{sym}] entry fail: {e}")
        return

    time.sleep(0.4)
    price = float(_client.futures_mark_price(symbol=sym)["markPrice"])

    tp = price + 0.7 * up if side == "LONG" else price - 0.7 * down
    sl = price - 0.4 * up if side == "LONG" else price + 0.4 * down

    tp_q, _ = apply_precision(sf, tp, qty)
    sl_q, _ = apply_precision(sf, sl, qty)

    try:
        _client.futures_create_order(symbol=sym, side=exit_side, type="STOP_MARKET", stopPrice=float(sl_q), closePosition=True, workingType="MARK_PRICE")
        _client.futures_create_order(symbol=sym, side=exit_side, type="TAKE_PROFIT_MARKET", stopPrice=float(tp_q), closePosition=True, workingType="MARK_PRICE")
        print(f"[{sym}] TP={float(tp_q):.4f} SL={float(sl_q):.4f}")
    except Exception as e:
        print(f"[{sym}] bracket fail: {e}")

    _state[sym] = {"entry": price, "tp": tp, "sl": sl, "side": side}


def _trail(sym, price, up, down):
    if sym not in _state:
        print(f"[{sym}] HOLD (missing)")
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
    sf = _fmap[sym]
    qty = float(_client.futures_position_information()[0]["positionAmt"])

    tp_q, _ = apply_precision(sf, st["tp"], abs(qty))
    sl_q, _ = apply_precision(sf, st["sl"], abs(qty))

    try:
        _client.futures_create_order(symbol=sym, side=exit_side, type="STOP_MARKET", stopPrice=float(sl_q), closePosition=True, workingType="MARK_PRICE")
        _client.futures_create_order(symbol=sym, side=exit_side, type="TAKE_PROFIT_MARKET", stopPrice=float(tp_q), closePosition=True, workingType="MARK_PRICE")
        print(f"[{sym}] TRAIL SET tp={tp_q} sl={sl_q}")
    except Exception as e:
        print(f"[{sym}] TRAIL ERR: {e}")


def process_signal(sym, price, up, down):
    side = _decide(sym, up, down, price)

    if _has_position(sym):
        _trail(sym, price, up, down)
        print(f"[{sym}] HOLD (trail)")
        return

    if side is None:
        print(f"[{sym}] HOLD (no signal)")
        return

    _place(sym, side, price, up, down)
