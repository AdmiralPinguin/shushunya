# -*- coding: utf-8 -*-
# trade_bot_fixed/stats_csv.py
import os
import csv
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional

CSV_PATH = os.path.join("stats", "trades.csv")

# ---- допустимые имена колонок ----
COL_TS     = {"ts", "time", "timestamp", "datetime"}
COL_SYMBOL = {"symbol", "ticker", "sym"}
COL_SIDE   = {"side", "direction"}
COL_QTY    = {"qty", "quantity", "size", "amount"}
COL_ENTRY  = {"entry", "entry_price", "open_price", "price_in", "in_price"}
COL_EXIT   = {"exit", "exit_price", "close_price", "price_out", "out_price"}
COL_REASON = {"reason", "tag", "why"}
COL_PNL    = {"pnl", "profit", "pl", "p_l"}

def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        try:
            return float(str(x).replace(",", "."))
        except Exception:
            return float(default)

def _detect_delimiter(path: str) -> str:
    # пробуем угадать ; или ,
    with open(path, "r", encoding="utf-8") as f:
        sample = f.read(2048)
    if sample.count(";") > sample.count(","):
        return ";"
    return ","

def _index_map(header: List[str]) -> Dict[str, int]:
    idx = {}
    norm = [h.strip().lower() for h in header]

    def find(candidates: set[str]) -> Optional[int]:
        for i, h in enumerate(norm):
            if h in candidates:
                return i
        return None

    idx["ts"]     = find(COL_TS)
    idx["symbol"] = find(COL_SYMBOL)
    idx["side"]   = find(COL_SIDE)
    idx["qty"]    = find(COL_QTY)
    idx["entry"]  = find(COL_ENTRY)
    idx["exit"]   = find(COL_EXIT)
    idx["reason"] = find(COL_REASON)
    idx["pnl"]    = find(COL_PNL)
    return idx

def _parse_ts(s: str) -> datetime:
    s = (s or "").strip()
    # поддержим ISO, а также "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    # fallback: сейчас
    return datetime.now(timezone.utc).astimezone()

def _pnl_from_fields(side: str, qty: float, entry: float, exit: float) -> float:
    side = (side or "").upper()
    if side == "LONG":
        return (exit - entry) * qty
    if side == "SHORT":
        return (entry - exit) * qty
    # если side неизвестен — считаем как long
    return (exit - entry) * qty

def _load_all() -> List[Dict[str, Any]]:
    if not os.path.exists(CSV_PATH):
        return []
    delim = _detect_delimiter(CSV_PATH)
    out: List[Dict[str, Any]] = []
    skipped = 0

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        r = csv.reader(f, delimiter=delim)
        header = next(r, None)
        if not header:
            return []
        imap = _index_map(header)

        for row in r:
            if not row or all(not str(x).strip() for x in row):
                continue
            try:
                ts = _parse_ts(row[imap["ts"]]) if imap["ts"] is not None else datetime.now(timezone.utc).astimezone()
                symbol = str(row[imap["symbol"]]).strip() if imap["symbol"] is not None else ""
                side = str(row[imap["side"]]).strip().upper() if imap["side"] is not None else "LONG"
                qty = _to_float(row[imap["qty"]]) if imap["qty"] is not None else 0.0
                entry = _to_float(row[imap["entry"]]) if imap["entry"] is not None else 0.0
                exitp = _to_float(row[imap["exit"]]) if imap["exit"] is not None else 0.0
                reason = str(row[imap["reason"]]).strip() if imap["reason"] is not None else ""
                pnl = None
                if imap["pnl"] is not None:
                    pnl = _to_float(row[imap["pnl"]], None)
                if pnl is None:
                    pnl = _pnl_from_fields(side, qty, entry, exitp)

                # sanity: если qty/entry/exit мусорные — пропускаем
                if qty <= 0 or entry <= 0 or exitp <= 0:
                    # иногда CSV логирует строки без сделки — скипаем
                    skipped += 1
                    continue

                out.append({
                    "ts": ts,
                    "symbol": symbol,
                    "side": side,
                    "qty": float(qty),
                    "entry": float(entry),
                    "exit": float(exitp),
                    "reason": reason,
                    "pnl": float(pnl),
                })
            except Exception:
                skipped += 1
                continue

    # можно здесь при желании логировать сколько строк пропущено — но этот модуль без logger, оставим тихо
    return out

def _in_same_day(ts: datetime, now: datetime) -> bool:
    return ts.astimezone().date() == now.astimezone().date()

def _week_year(ts: datetime) -> Tuple[int, int]:
    iso = ts.isocalendar()
    return (iso[0], iso[1])  # year, week

def _month_year(ts: datetime) -> Tuple[int, int]:
    lt = ts.astimezone()
    return (lt.year, lt.month)

def _agg(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    losses = sum(1 for r in rows if r["pnl"] < 0)
    pnl = sum(r["pnl"] for r in rows)
    profit = sum(r["pnl"] for r in rows if r["pnl"] > 0)
    loss = sum(-r["pnl"] for r in rows if r["pnl"] < 0)
    avg_win = (profit / wins) if wins > 0 else 0.0
    avg_loss = (loss / losses) if losses > 0 else 0.0
    winrate = (wins / max(1, n)) * 100.0
    return {
        "trades": n,
        "winrate": winrate,
        "profit": profit,
        "loss": loss,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "pnl": pnl,
        "fees": 0.0,       # TODO: можно подтянуть из futures_account_trades и вычесть
        "retr_win": 0.0,   # TODO: если будешь писать reason=RETRACE — можем посчитать
    }

def compute_snapshots(now: datetime | None = None) -> Dict[str, Dict[str, Any]]:
    now = now or datetime.now(timezone.utc).astimezone()
    rows = _load_all()
    day_rows = [r for r in rows if _in_same_day(r["ts"], now)]
    y_w = _week_year(now)
    week_rows = [r for r in rows if _week_year(r["ts"]) == y_w]
    y_m = _month_year(now)
    month_rows = [r for r in rows if _month_year(r["ts"]) == y_m]
    return {
        "DAY": _agg(day_rows),
        "WEEK": _agg(week_rows),
        "MONTH": _agg(month_rows),
        "ALL": _agg(rows),
    }
