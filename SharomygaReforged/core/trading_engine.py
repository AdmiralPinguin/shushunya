from core.logger import log

def execute_trade(symbol, direction, up_pred, down_pred, current_price):
    if abs(direction) < 0.0001:
        log(f"{symbol} — сигнал слабый, пропуск.")
        return

    if direction > 0:
        tp = current_price + up_pred * 0.7
        sl = current_price - up_pred * 0.4
        side = "BUY"
    else:
        tp = current_price - down_pred * 0.7
        sl = current_price + down_pred * 0.4
        side = "SELL"

    log(f"{symbol}: {side} | TP={tp:.2f} | SL={sl:.2f} | Δ={direction:.4f}")
