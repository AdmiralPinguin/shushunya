import time, datetime

def wait_next_cycle(cycle_minutes: int):
    """Выравнивание с Binance временем"""
    now = datetime.datetime.utcnow()
    minutes = (now.minute // cycle_minutes + 1) * cycle_minutes
    next_cycle = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=minutes)
    sleep_time = (next_cycle - now).total_seconds()
    if sleep_time < 0:
        sleep_time += cycle_minutes * 60
    time.sleep(sleep_time)
