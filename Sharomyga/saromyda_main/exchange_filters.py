from decimal import Decimal, ROUND_DOWN

def _dec(x): return Decimal(str(x))

def round_step(x: float, step: float) -> float:
    step = _dec(step)
    return float((_dec(x) / step).quantize(0, rounding=ROUND_DOWN) * step)

def round_price(price: float, tick_size: float) -> float:
    return round_step(price, tick_size)

def round_qty(qty: float, step_size: float) -> float:
    return round_step(qty, step_size)

def get_symbol_filters(client, symbol: str):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            f = {flt["filterType"]: flt for flt in s["filters"]}
            tick = float(f["PRICE_FILTER"]["tickSize"])
            step = float(f["LOT_SIZE"]["stepSize"])
            min_notional = float(f.get("MIN_NOTIONAL", {}).get("notional", 0.0))
            return tick, step, min_notional
    raise ValueError(f"Symbol {symbol} not found in exchangeInfo")
