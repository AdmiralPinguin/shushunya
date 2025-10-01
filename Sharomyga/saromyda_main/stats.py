import os
import csv
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import pandas as pd


CSV_COLUMNS = [
    "ts",            # ISO timestamp close
    "symbol",
    "side",          # LONG/SHORT
    "qty",           # abs qty closed
    "entry",         # avg entry price for closed chunk
    "exit",          # exit price
    "pnl_usd",       # gross pnl for this close (without fees)
    "fees_usd",      # total fees (entry+exit) for this close
    "rpnl_usd",      # net pnl (pnl_usd - fees_usd)
    "reason",        # "", TP, SL, RETRACE, MANUAL, PARTIAL, etc.
]

# Утилита: безопасное приведение
def _f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class StatsTracker:
    """
    Пишет сделки в CSV и делает агрегаты по DAY/WEEK/MONTH/ALL:
      - trades, winrate, profit_sum (net), loss_sum (net), avg_win, avg_loss, fees, pnl (net)
      - micro_trail_win_pct: % выигрышных сделок с reason == 'RETRACE'
    API:
      - observe(symbol, qty, entry_price, mark_price): телеметрия (необязательно)
      - record_trade(...): ручная запись закрытия (опционально)
      - tag_close_reason(symbol, reason): пометить причину для следующей/последней записи
      - recompute_and_persist(): перечитать CSV и пересчитать сводки
      - mini_summary(): короткая строка со сводками
    """
    def __init__(self, base_dir: str = "stats", fee_taker: float = 0.0002, enabled: bool = True, logger=None):
        self.base_dir = base_dir
        self.csv_path = os.path.join(self.base_dir, "trades.csv")
        self.enabled = enabled
        self.fee_taker = float(fee_taker)
        self.log = logger
        self._last_summary_lines: List[str] = []
        self._last_close_reason: Dict[str, str] = {}  # symbol -> reason (к следующей записи)
        self._telemetry: Dict[str, Dict[str, Any]] = {}  # свободная форма

        os.makedirs(self.base_dir, exist_ok=True)
        self._ensure_csv()

    # ------------- публичные методы -------------

    def observe(self, symbol: str, qty: float, entry_price: float, mark_price: float):
        """Лёгкая телеметрия — можно игнорировать. Ничего не пишет, просто запоминает последнее."""
        if not self.enabled:
            return
        self._telemetry[symbol] = {
            "qty": float(qty),
            "entry": float(entry_price),
            "mark": float(mark_price),
            "ts": _now_iso(),
        }

    def tag_close_reason(self, symbol: str, reason: str):
        """
        Пометить причину закрытия. Если следующая запись сделки прилетит — она подставит reason.
        Плюс попробуем протащить reason в последнюю запись для этого символа, если у неё reason пустой.
        """
        if not self.enabled:
            return
        reason = (reason or "").strip().upper()
        if not reason:
            return
        self._last_close_reason[symbol] = reason
        try:
            self._retrofill_reason_for_symbol(symbol, reason)
        except Exception as e:
            if self.log:
                self.log.debug(f"[STATS] tag reason retrofill fail for {symbol}: {e}")

    def record_trade(self, symbol: str, side: str, qty: float,
                     entry_price: float, exit_price: float,
                     fees_usd: Optional[float] = None, reason: Optional[str] = ""):
        """
        Ручная запись закрытия (если нужно). Если fees_usd не переданы — оценим как taker*2*notional.
        """
        if not self.enabled:
            return
        qty = abs(float(qty))
        entry_price = float(entry_price)
        exit_price = float(exit_price)
        notional = max(qty * ((entry_price + exit_price) / 2.0), 0.0)

        if fees_usd is None:
            fees_usd = notional * self.fee_taker * 2.0  # вход+выход как такер
        pnl_usd = (exit_price - entry_price) * qty if side.upper() == "LONG" else (entry_price - exit_price) * qty
        rpnl_usd = pnl_usd - fees_usd
        reason = (reason or self._last_close_reason.pop(symbol, "")).upper()

        row = {
            "ts": _now_iso(),
            "symbol": symbol,
            "side": side.upper(),
            "qty": qty,
            "entry": entry_price,
            "exit": exit_price,
            "pnl_usd": pnl_usd,
            "fees_usd": fees_usd,
            "rpnl_usd": rpnl_usd,
            "reason": reason,
        }
        self._append_csv_row(row)
        if self.log:
            self.log.info(f"[STATS] {symbol} CLOSE {side.upper()} qty={qty} entry={entry_price} exit={exit_price} pnl={pnl_usd:.4f} (fees {fees_usd:.4f})")

    def recompute_and_persist(self):
        """Перечитать trades.csv и пересчитать сводки по DAY/WEEK/MONTH/ALL."""
        if not self.enabled:
            self._last_summary_lines = []
            return

        df = self._read_df()
        # Нормализация: гарантируем наличие всех колонок
        df = self._normalize_df(df)

        lines = []

        def _agg(df_slice: pd.DataFrame, label: str) -> str:
            if df_slice.empty:
                return f"{label}: trades=0 | winrate=0.0% | profit=0.00 | loss=0.00 | avg_win=0.00 | avg_loss=0.00 | pnl=0.00 | fees=0.00 | retr_win=0.0%"

            trades = len(df_slice)
            rpnl = df_slice["rpnl_usd"]
            fees = df_slice["fees_usd"]

            wins_mask = rpnl > 0
            losses_mask = rpnl < 0

            wins = int(wins_mask.sum())
            losses = int(losses_mask.sum())

            profit_sum = float(rpnl[wins_mask].sum()) if wins > 0 else 0.0
            loss_sum = float(rpnl[losses_mask].sum()) if losses > 0 else 0.0  # уже отрицательное
            pnl_all = float(rpnl.sum())
            fees_all = float(fees.sum())

            avg_win = float(rpnl[wins_mask].mean()) if wins > 0 else 0.0
            avg_loss = float(rpnl[losses_mask].mean()) if losses > 0 else 0.0

            winrate = (wins / trades * 100.0) if trades > 0 else 0.0

            # доля победных, закрытых по RETRACE
            retr_win_pct = 0.0
            if "reason" in df_slice.columns:
                retr_wins = int(((rpnl > 0) & (df_slice["reason"].str.upper() == "RETRACE")).sum())
                retr_win_pct = (retr_wins / wins * 100.0) if wins > 0 else 0.0

            return (f"{label}: trades={trades} | winrate={winrate:.1f}% | profit={profit_sum:.2f} | "
                    f"loss={loss_sum:.2f} | avg_win={avg_win:.2f} | avg_loss={avg_loss:.2f} | "
                    f"pnl={pnl_all:.2f} | fees={fees_all:.2f} | retr_win={retr_win_pct:.1f}%")

        # DAY/WEEK/MONTH/ALL
        if not df.empty:
            df = df.copy()
            # разбор времени: если tz-aware → в локалку; если нет — coerce
            try:
                ts_parsed = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(None)
            except Exception:
                ts_parsed = pd.to_datetime(df["ts"], errors="coerce")
            df["date"] = ts_parsed.dt.date
            df["year"] = ts_parsed.dt.isocalendar().year
            df["week"] = ts_parsed.dt.isocalendar().week
            df["month"] = ts_parsed.dt.to_period("M").astype(str)

            today = datetime.now().date()
            df_day = df[df["date"] == today]
            lines.append(_agg(df_day, f"DAY {today.isoformat()}"))

            iso = datetime.now().isocalendar()
            df_week = df[(df["year"] == iso.year) & (df["week"] == iso.week)]
            lines.append(_agg(df_week, f"WEEK {iso.year}-W{iso.week:02d}"))

            curr_month = datetime.now().strftime("%Y-%m")
            df_month = df[df["month"] == curr_month]
            lines.append(_agg(df_month, f"MONTH {curr_month}"))

            lines.append(_agg(df, "ALL ALL"))
        else:
            today = datetime.now().date().isoformat()
            iso = datetime.now().isocalendar()
            month = datetime.now().strftime("%Y-%m")
            lines.append(f"DAY {today}: trades=0 | winrate=0.0% | profit=0.00 | loss=0.00 | avg_win=0.00 | avg_loss=0.00 | pnl=0.00 | fees=0.00 | retr_win=0.0%")
            lines.append(f"WEEK {iso.year}-W{iso.week:02d}: trades=0 | winrate=0.0% | profit=0.00 | loss=0.00 | avg_win=0.00 | avg_loss=0.00 | pnl=0.00 | fees=0.00 | retr_win=0.0%")
            lines.append(f"MONTH {month}: trades=0 | winrate=0.0% | profit=0.00 | loss=0.00 | avg_win=0.00 | avg_loss=0.00 | pnl=0.00 | fees=0.00 | retr_win=0.0%")
            lines.append("ALL ALL: trades=0 | winrate=0.0% | profit=0.00 | loss=0.00 | avg_win=0.00 | avg_loss=0.00 | pnl=0.00 | fees=0.00 | retr_win=0.0%")

        self._last_summary_lines = lines

    def mini_summary(self) -> str:
        """
        Короткий вывод в логи. Пример:
          STATS: trades=3 | winrate=66.7% | pnl=-0.01 USDT (day 2025-08-09)
          DAY 2025-08-09: trades=... | ...
          WEEK 2025-W32: ...
          MONTH 2025-08: ...
          ALL ALL: ...
        """
        if not self.enabled:
            return "STATS disabled"
        if not self._last_summary_lines:
            self.recompute_and_persist()

        head = self._head_line_from_summary(self._last_summary_lines)
        lines = [head] + self._last_summary_lines
        return "\n".join(lines)

    # ------------- внутренности -------------

    def _ensure_csv(self):
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                w.writeheader()

    def _append_csv_row(self, row: Dict[str, Any]):
        self._ensure_csv()
        # Если reason не задан, попробуем взять “последний тэг” для этого символа
        if not row.get("reason"):
            sym = row.get("symbol", "")
            if sym in self._last_close_reason:
                row["reason"] = self._last_close_reason.pop(sym)
        # Приведём типы/формат
        out = {
            "ts": row.get("ts", _now_iso()),
            "symbol": row.get("symbol", ""),
            "side": (row.get("side", "") or "").upper(),
            "qty": f"{_f(row.get('qty', 0.0)):.8f}",
            "entry": f"{_f(row.get('entry', 0.0)):.8f}",
            "exit": f"{_f(row.get('exit', 0.0)):.8f}",
            "pnl_usd": f"{_f(row.get('pnl_usd', 0.0)):.8f}",
            "fees_usd": f"{_f(row.get('fees_usd', 0.0)):.8f}",
            "rpnl_usd": f"{_f(row.get('rpnl_usd', 0.0)):.8f}",
            "reason": (row.get("reason") or "").upper(),
        }
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            w.writerow(out)

    def _read_df(self) -> pd.DataFrame:
        self._ensure_csv()
        try:
            df = pd.read_csv(self.csv_path)
        except Exception:
            df = pd.DataFrame(columns=CSV_COLUMNS)
        return df

    def _normalize_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Делает df совместимым с новой схемой.
        Если старые CSV без нужных колонок — добавим их с дефолтами.
        """
        if df is None or df.empty:
            # гарантируем схему даже на пустой таблице
            return pd.DataFrame(columns=CSV_COLUMNS)

        cols = list(df.columns)

        # --- Legacy compatibility for executor stats ---
        # Some external modules (e.g. FuturesExecutor) may write to the same CSV using
        # a simplified schema with a single 'pnl' column instead of separate
        # 'pnl_usd', 'fees_usd' and 'rpnl_usd'.  If we detect such a scenario,
        # derive the missing fields so that winrate and PnL computations remain correct.
        # We perform this derivation early to ensure subsequent type conversions apply.
        if "pnl_usd" not in cols and "rpnl_usd" not in cols and "pnl" in cols:
            # Make a copy to avoid modifying original reference
            df = df.copy()
            # Convert 'pnl' to numeric and treat it as net PnL (no fees available)
            df["pnl_usd"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
            # If fees_usd missing, assume zero fees for legacy entries
            if "fees_usd" not in df.columns:
                df["fees_usd"] = 0.0
            else:
                df["fees_usd"] = pd.to_numeric(df["fees_usd"], errors="coerce").fillna(0.0)
            # rpnl_usd (net PnL) is pnl_usd minus fees
            df["rpnl_usd"] = df["pnl_usd"] - df["fees_usd"]
            cols = list(df.columns)

        # Если первая строка попала как хедер (кривой CSV) — попытаемся перечитать без хэдера (мягко)
        if "ts" not in cols and len(cols) == len(CSV_COLUMNS) - 1 and "symbol" in cols and "side" in cols:
            # похоже на старую схему без reason -> добавим reason
            df = df.copy()
            df["reason"] = ""
        elif "ts" not in cols:
            # Совсем старая/нестандартная схема — добавим все недостающие поля
            df = df.copy()
            for c in CSV_COLUMNS:
                if c not in df.columns:
                    if c == "ts":
                        df[c] = _now_iso()
                    elif c in ("symbol", "side", "reason"):
                        df[c] = ""
                    else:
                        df[c] = 0.0

        # Приведём типы
        for col in ("qty", "entry", "exit", "pnl_usd", "fees_usd", "rpnl_usd"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        if "reason" in df.columns:
            df["reason"] = df["reason"].fillna("").astype(str)

        # Убедимся, что столбцы упорядочены как в схеме (для единообразия записи)
        for c in CSV_COLUMNS:
            if c not in df.columns:
                df[c] = "" if c in ("ts", "symbol", "side", "reason") else 0.0
        return df[CSV_COLUMNS]

    def _retrofill_reason_for_symbol(self, symbol: str, reason: str):
        """
        Пытаемся проставить reason для последней записи символа, если у неё reason пустой.
        """
        if not os.path.exists(self.csv_path):
            return
        df = self._read_df()
        df = self._normalize_df(df)
        if df.empty or "symbol" not in df.columns:
            return
        mask = (df["symbol"] == symbol)
        if not mask.any():
            return
        idx = df[mask].index.max()
        if "reason" in df.columns:
            cur = str(df.at[idx, "reason"]).strip()
            if cur == "":
                df.at[idx, "reason"] = reason
                df.to_csv(self.csv_path, index=False)

    def _head_line_from_summary(self, lines: List[str]) -> str:
        """
        Строим первую компактную строку: "STATS: trades=... | winrate=... | pnl=..."
        Берём показатели из первой строки (DAY ...).
        """
        if not lines:
            return "STATS: trades=0 | winrate=0.0% | pnl=0.00 USDT"
        day_line = lines[0]
        # вытащим кусочки
        try:
            parts = day_line.split("|")
            trades_part = next((p for p in parts if "trades=" in p), "").strip()
            win_part = next((p for p in parts if "winrate=" in p), "").strip()
            pnl_part = next((p for p in parts if " pnl=" in p or p.strip().startswith("pnl=")), "").strip()
            if trades_part and win_part and pnl_part:
                if not pnl_part.endswith("USDT"):
                    pnl_part = pnl_part + " USDT"
                return f"STATS: {trades_part} | {win_part} | {pnl_part}"
        except Exception:
            pass
        return "STATS: trades=0 | winrate=0.0% | pnl=0.00 USDT"
