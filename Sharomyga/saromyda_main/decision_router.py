# -*- coding: utf-8 -*-
# trade_bot_fixed/decision_router.py
from __future__ import annotations

from . import config as cfg

# Ожидаем, что compute_votes() возвращает объект с:
# v.long_entry, v.short_entry, v.long_exit, v.short_exit  (float, "голоса")

def _bool_str(ok: bool) -> str:
    return "✓" if ok else "✗"

def explain_decision(v, has_long: bool = False, has_short: bool = False) -> str:
    """
    Короткое объяснение, какие именно чеки пройдены/завалены.
    Логика гейтов (как договаривались):
      - ВХОД ЛОНГ:  LE >= LONG_ENTRY_MIN_BUY  И  LX <= LONG_ENTRY_MAX_LX
      - ВХОД ШОРТ: SE >= SHORT_ENTRY_MIN_SELL И  SX <= SHORT_ENTRY_MAX_SX
      - ВЫХОД ЛОНГ: LX >= LONG_EXIT_MIN_SELL  И  LE <= LONG_EXIT_MAX_LE
      - ВЫХОД ШОРТ: SX >= SHORT_EXIT_MIN_BUY  И  SE <= SHORT_EXIT_MAX_SE
      - FLIP включаем, если противоположный вход "hard" и не заблокирован его собственным выходным гейтом.
    """
    # Пороги (берём из конфига, чтобы не ловить опечатки)
    LE_H  = float(getattr(cfg, "LONG_ENTRY_MIN_BUY", 6.0))
    LE_S  = float(getattr(cfg, "LONG_ENTRY_SOFT_BUY", 5.5))
    LX_G  = float(getattr(cfg, "LONG_ENTRY_MAX_LX", 2.5))

    SE_H  = float(getattr(cfg, "SHORT_ENTRY_MIN_SELL", 6.0))
    SE_S  = float(getattr(cfg, "SHORT_ENTRY_SOFT_SELL", 5.5))
    SX_G  = float(getattr(cfg, "SHORT_ENTRY_MAX_SX", 2.5))

    LX_H  = float(getattr(cfg, "LONG_EXIT_MIN_SELL", 4.0))
    LE_G  = float(getattr(cfg, "LONG_EXIT_MAX_LE", 2.5))

    SX_H  = float(getattr(cfg, "SHORT_EXIT_MIN_BUY", 4.0))
    SE_G  = float(getattr(cfg, "SHORT_EXIT_MAX_SE", 2.5))

    # Хард/софт входы
    hard_long  = (v.long_entry  >= LE_H) and (v.long_exit  <= LX_G)
    soft_long  = (v.long_entry  >= LE_S) and (v.long_entry <  LE_H) and (v.long_exit <= LX_G)

    hard_short = (v.short_entry >= SE_H) and (v.short_exit <= SX_G)
    soft_short = (v.short_entry >= SE_S) and (v.short_entry <  SE_H) and (v.short_exit <= SX_G)

    # Выходы (с учетом вето своим входом)
    close_long  = (v.long_exit  >= LX_H) and (v.long_entry <= LE_G)
    close_short = (v.short_exit >= SX_H) and (v.short_entry <= SE_G)

    # Флипы
    flip_to_short = has_long  and hard_short and (v.short_exit <= SX_G)
    flip_to_long  = has_short and hard_long  and (v.long_exit  <= LX_G)

    parts = []
    # Входные гейты
    parts.append(f"LE {v.long_entry:.2f}≥{LE_H:.2f}:{_bool_str(v.long_entry>=LE_H)} "
                 f"LX {v.long_exit:.2f}≤{LX_G:.2f}:{_bool_str(v.long_exit<=LX_G)}")
    parts.append(f"SE {v.short_entry:.2f}≥{SE_H:.2f}:{_bool_str(v.short_entry>=SE_H)} "
                 f"SX {v.short_exit:.2f}≤{SX_G:.2f}:{_bool_str(v.short_exit<=SX_G)}")
    # Выходные гейты
    parts.append(f"LX {v.long_exit:.2f}≥{LX_H:.2f}:{_bool_str(v.long_exit>=LX_H)} "
                 f"LE {v.long_entry:.2f}≤{LE_G:.2f}:{_bool_str(v.long_entry<=LE_G)}")
    parts.append(f"SX {v.short_exit:.2f}≥{SX_H:.2f}:{_bool_str(v.short_exit>=SX_H)} "
                 f"SE {v.short_entry:.2f}≤{SE_G:.2f}:{_bool_str(v.short_entry<=SE_G)}")

    # Состояние позиций
    parts.append(f"posL:{_bool_str(has_long)} posS:{_bool_str(has_short)} "
                 f"hardL:{_bool_str(hard_long)} hardS:{_bool_str(hard_short)} "
                 f"softL:{_bool_str(soft_long)} softS:{_bool_str(soft_short)} "
                 f"flipL:{_bool_str(flip_to_long)} flipS:{_bool_str(flip_to_short)} "
                 f"closeL:{_bool_str(close_long)} closeS:{_bool_str(close_short)}")

    return " | ".join(parts)

def plan_action(v, has_long: bool = False, has_short: bool = False) -> str:
    """
    Возвращает одно из:
      - "FLIP_TO_SHORT" / "FLIP_TO_LONG"
      - "CLOSE_LONG" / "CLOSE_SHORT"
      - "OPEN_LONG" / "OPEN_SHORT"
      - "HOLD"
    Софт-входы (0.5x) НЕ возвращаем — ими управляет main.py на основе soft_* флагов.
    """
    ALLOW_SHORTS    = bool(getattr(cfg, "ALLOW_SHORTS", True))
    FLIP_ON_OPPOSITE = bool(getattr(cfg, "FLIP_ON_OPPOSITE", True))

    # Пороги (как в explain_decision)
    LE_H  = float(getattr(cfg, "LONG_ENTRY_MIN_BUY", 6.0))
    LX_G  = float(getattr(cfg, "LONG_ENTRY_MAX_LX", 2.5))
    SE_H  = float(getattr(cfg, "SHORT_ENTRY_MIN_SELL", 6.0))
    SX_G  = float(getattr(cfg, "SHORT_ENTRY_MAX_SX", 2.5))
    LX_H  = float(getattr(cfg, "LONG_EXIT_MIN_SELL", 4.0))
    LE_G  = float(getattr(cfg, "LONG_EXIT_MAX_LE", 2.5))
    SX_H  = float(getattr(cfg, "SHORT_EXIT_MIN_BUY", 4.0))
    SE_G  = float(getattr(cfg, "SHORT_EXIT_MAX_SE", 2.5))

    hard_long   = (v.long_entry  >= LE_H) and (v.long_exit  <= LX_G)
    hard_short  = (v.short_entry >= SE_H) and (v.short_exit <= SX_G)
    close_long  = (v.long_exit  >= LX_H) and (v.long_entry <= LE_G)
    close_short = (v.short_exit >= SX_H) and (v.short_entry <= SE_G)

    if has_long:
        if FLIP_ON_OPPOSITE and ALLOW_SHORTS and hard_short and (v.short_exit <= SX_G):
            return "FLIP_TO_SHORT"
        if close_long:
            return "CLOSE_LONG"
        return "HOLD"

    if has_short:
        if FLIP_ON_OPPOSITE and hard_long and (v.long_exit <= LX_G):
            return "FLIP_TO_LONG"
        if close_short:
            return "CLOSE_SHORT"
        return "HOLD"

    # flat
    if hard_long and (v.long_exit <= LX_G):
        return "OPEN_LONG"
    if ALLOW_SHORTS and hard_short and (v.short_exit <= SX_G):
        return "OPEN_SHORT"

    return "HOLD"
