# Безопасные простые паттерны, без синтаксиса-ловушек
def is_hammer(o, h, l, c) -> bool:
    body = abs(c - o)
    lower = o if c >= o else c
    tail = lower - l
    return tail > body * 2 and (h - max(o,c)) <= body

def is_engulfing(o1,c1,o2,c2) -> bool:
    # Bullish engulfing: prev down, now up, and now body engulfs prev body
    prev_down = c1 < o1
    now_up = c2 > o2
    return prev_down and now_up and (o2 <= c1) and (c2 >= o1)

def is_morning_star(o1,c1,o2,c2,o3,c3) -> bool:
    # very naive MS: down big, small gap, strong up close into body1
    return (c1 < o1) and abs(c2 - o2) < abs(c1 - o1)*0.3 and (c3 > o3) and (c3 >= (o1 + c1)/2)
