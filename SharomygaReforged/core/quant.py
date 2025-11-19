from __future__ import annotations
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, getcontext
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# высокую точность для операций шагами типа 0.0001
getcontext().prec = 28

@dataclass
class SymbolFilters:
    symbol: str
    tick_size: Decimal                 # PRICE_FILTER.tickSize
    step_size: Decimal                 # LOT_SIZE.stepSize
    min_qty: Decimal                   # LOT_SIZE.minQty
    max_qty: Decimal                   # LOT_SIZE.maxQty
    min_notional: Optional[Decimal]    # MIN_NOTIONAL.minNotional / notional
    price_precision: int               # можно вывести из tick_size
    quantity_precision: int            # можно вывести из step_size

def _dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))

def _precision_from_step(step: Decimal) -> int:
    # количество знаков после запятой из шага
    s = format(step.normalize(), 'f')
    return len(s.split('.')[-1]) if '.' in s else 0

def parse_futures_exchange_info(exchange_info: dict) -> Dict[str, SymbolFilters]:
    res: Dict[str, SymbolFilters] = {}
    for s in exchange_info.get("symbols", []):
        sym = s["symbol"]
        tick_size = step_size = min_qty = max_qty = None
        min_notional = None
        for f in s.get("filters", []):
            t = f.get("filterType")
            if t == "PRICE_FILTER":
                tick_size = _dec(f["tickSize"])
            elif t == "LOT_SIZE":
                step_size = _dec(f["stepSize"])
                min_qty = _dec(f["minQty"])
                max_qty = _dec(f["maxQty"])
            elif t == "MIN_NOTIONAL":
                # на фьючерсах поле иногда называется notional
                val = f.get("minNotional", f.get("notional"))
                if val is not None:
                    min_notional = _dec(val)

        if not (tick_size and step_size and min_qty is not None and max_qty is not None):
            # пропускаем неактивные или неполные
            continue

        price_precision = _precision_from_step(tick_size)
        quantity_precision = _precision_from_step(step_size)

        res[sym] = SymbolFilters(
            symbol=sym,
            tick_size=tick_size,
            step_size=step_size,
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
            price_precision=price_precision,
            quantity_precision=quantity_precision,
        )
    return res

def quantize_to_step(value, step: Decimal, mode: str = "floor") -> Decimal:
    """
    Привязка к шагу.
    mode: 'floor' (кратность вниз), 'round' (обычное округление), 'ceil' (вверх).
    """
    v = _dec(value)
    step = _dec(step)
    if step == 0:
        return v

    q = (v / step)
    if mode == "round":
        q = q.quantize(Decimal(0), rounding=ROUND_HALF_UP)
    elif mode == "ceil":
        # ceil(x) = -floor(-x)
        q = (-q).quantize(Decimal(0), rounding=ROUND_DOWN) * Decimal(-1)
    else:
        # floor к шагу
        q = q.quantize(Decimal(0), rounding=ROUND_DOWN)
    return q * step

def q_price(symf: SymbolFilters, price, mode: str = "floor") -> Decimal:
    p = quantize_to_step(price, symf.tick_size, mode=mode)
    # формат по точности цены
    fmt = f"{{0:.{symf.price_precision}f}}"
    return _dec(fmt.format(p))

def q_qty(symf: SymbolFilters, qty, mode: str = "floor") -> Decimal:
    q = quantize_to_step(qty, symf.step_size, mode=mode)
    # зажать в [min_qty, max_qty]
    if q < symf.min_qty:
        q = symf.min_qty
    if q > symf.max_qty:
        q = symf.max_qty
    fmt = f"{{0:.{symf.quantity_precision}f}}"
    return _dec(fmt.format(q))

def ensure_min_notional(symf: SymbolFilters, price, qty, mode: str = "ceil") -> Tuple[Decimal, Decimal]:
    """
    Если minNotional задан, увеличивает qty до минимума нотации.
    Возвращает (price_q, qty_q) уже квантованные.
    """
    price_q = q_price(symf, price, mode="round")
    qty_q = q_qty(symf, qty, mode=mode)

    if symf.min_notional:
        notional = price_q * qty_q
        if notional < symf.min_notional:
            need = symf.min_notional / (price_q if price_q > 0 else _dec("1"))
            qty_q = q_qty(symf, need, mode="ceil")

    return price_q, qty_q

def apply_precision(symf: SymbolFilters, price, qty, notional_guard: bool = True) -> Tuple[Decimal, Decimal]:
    """
    Полный проход: цена и количество к шагам и проверка minNotional.
    Возвращает (price_q, qty_q).
    """
    if notional_guard:
        return ensure_min_notional(symf, price, qty)
    else:
        return q_price(symf, price), q_qty(symf, qty)

# ---- пример использования как скрипта ----
if __name__ == "__main__":
    # Локальный тест: подтянуть exchangeInfo через python-binance
    from dotenv import load_dotenv
    from binance.client import Client
    import os
    load_dotenv()
    client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))

    info = client.futures_exchange_info()
    fmap = parse_futures_exchange_info(info)

    for sym in ["ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        s = fmap[sym]
        # пример: хотим купить на $50 по рыночной цене
        price = Decimal(client.futures_mark_price(symbol=sym)["markPrice"])
        usd = Decimal("50")
        qty_raw = usd / price  # без плеча; плечо учитывай при расчете usd
        p_q, q_q = apply_precision(s, price, qty_raw, notional_guard=True)
        print(sym, "mark:", price, "->", "price:", p_q, "qty:", q_q)
