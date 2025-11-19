# trade_bot_fixed/exchange_utils.py
from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Dict, Tuple, Any, Optional

# Лёгкий кеш фильтров: symbol -> (tick, step, minQty, minNotional, price_decimals, qty_decimals)
_EX_CACHE: Dict[str, Tuple[float, float, float, float, int, int]] = {}


def _round_to(value: float, step: float, mode: str) -> float:
    """
    Универсальное округление к сетке step.
    mode: floor | ceil | round
    """
    if step <= 0:
        return float(value)
    v = Decimal(str(value)) / Decimal(str(step))
    if mode == "floor":
        v = v.to_integral_value(rounding=ROUND_FLOOR)
    elif mode == "ceil":
        v = v.to_integral_value(rounding=ROUND_CEILING)
    else:
        v = v.to_integral_value(rounding=ROUND_HALF_UP)
    return float(Decimal(v) * Decimal(str(step)))


def get_futures_filters(client, symbol: str) -> Tuple[float, float, float, float, int, int]:
    """
    Возвращает:
      tickSize, stepSize, minQty, minNotional, price_decimals, qty_decimals
    С кешированием.
    """
    key = symbol.upper()
    if key in _EX_CACHE:
        return _EX_CACHE[key]

    info = client.futures_exchange_info()
    data = None
    for s in info.get("symbols", []):
        if s.get("symbol") == key:
            data = s
            break
    if not data:
        # защитный дефолт (для моков/тестов)
        _EX_CACHE[key] = (0.01, 0.001, 0.001, 5.0, 2, 3)
        return _EX_CACHE[key]

    tick = 0.01
    step = 0.001
    min_qty = 0.0
    min_notional = 5.0
    price_dec = 2
    qty_dec = 3

    for f in data.get("filters", []):
        t = f.get("filterType")
        if t == "PRICE_FILTER":
            tick = float(f.get("tickSize", tick))
            price_dec = max(0, int(round(-math.log10(tick)))) if tick > 0 else 2
        elif t == "LOT_SIZE":
            step = float(f.get("stepSize", step))
            min_qty = float(f.get("minQty", min_qty))
            qty_dec = max(0, int(round(-math.log10(step)))) if step > 0 else 3
        elif t == "MIN_NOTIONAL":
            min_notional = float(f.get("notional", min_notional))

    _EX_CACHE[key] = (tick, step, min_qty, min_notional, price_dec, qty_dec)
    return _EX_CACHE[key]


def quantize_price(client, symbol: str, price: float, mode: str = "round") -> float:
    tick, *_ = get_futures_filters(client, symbol)
    return _round_to(price, tick, mode)


def quantize_qty(client, symbol: str, qty: float, mode: str = "down") -> float:
    _, step, min_qty, *_ = get_futures_filters(client, symbol)
    q = _round_to(qty, step, "floor" if mode in ("down", "floor") else "ceil")
    if q <= 0:
        q = step
    if q < min_qty:
        q = min_qty
        q = _round_to(q, step, "ceil")
    return float(q)


def min_notional_qty(client, symbol: str, price: float) -> float:
    """
    Возвращает минимально допустимую qty для выполнения minNotional при данной цене.
    """
    _, step, _, min_not, *_ = get_futures_filters(client, symbol)
    if price <= 0:
        return 0.0
    need = float(min_not) / float(price)
    return float(_round_to(need, step, "ceil"))


def fmt_price_str(client, symbol: str, price: float) -> str:
    tick, _, _, _, dec_p, _ = get_futures_filters(client, symbol)
    return f"{quantize_price(client, symbol, price, 'round'):.{dec_p}f}"


def fmt_qty_str(client, symbol: str, qty: float) -> str:
    _, step, _, _, _, dec_q = get_futures_filters(client, symbol)
    q = quantize_qty(client, symbol, qty, "down")
    return f"{q:.{dec_q}f}"
