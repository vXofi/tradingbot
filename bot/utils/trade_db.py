"""
SQLite persistence for closed positions.

Uses stdlib sqlite3 — no external dependency.  Single-row inserts on
close are sub-millisecond, so blocking the event loop briefly is fine.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import Position, PositionState, Sentiment

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS closed_positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    figi         TEXT    NOT NULL,
    ticker       TEXT    NOT NULL,
    direction    TEXT    NOT NULL,
    entry_price  REAL    NOT NULL,
    quantity     INTEGER NOT NULL,
    entry_time   TEXT    NOT NULL,
    stop_loss    REAL    NOT NULL,
    take_profit  REAL,
    atr          REAL    NOT NULL,
    peak_price   REAL    NOT NULL,
    exit_price   REAL,
    exit_time    TEXT,
    exit_reason  TEXT,
    pnl          REAL
)
"""

_INSERT = """
INSERT INTO closed_positions
    (figi, ticker, direction, entry_price, quantity, entry_time,
     stop_loss, take_profit, atr, peak_price,
     exit_price, exit_time, exit_reason, pnl)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT = """
SELECT figi, ticker, direction, entry_price, quantity, entry_time,
       stop_loss, take_profit, atr, peak_price,
       exit_price, exit_time, exit_reason, pnl
FROM closed_positions ORDER BY id DESC
"""


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _iso_to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s)


_SENTIMENT_MAP = {s.value: s for s in Sentiment}


class TradeDatabase:
    """Thin sqlite3 wrapper for persisting closed positions."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def open(self):
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def save_position(self, position: Position):
        """Persist a single closed position."""
        assert self._conn is not None, "TradeDatabase not opened"
        self._conn.execute(_INSERT, (
            position.figi,
            position.ticker,
            position.direction.value,
            position.entry_price,
            position.quantity,
            _dt_to_iso(position.entry_time),
            position.stop_loss,
            position.take_profit,
            position.atr,
            position.peak_price,
            position.exit_price,
            _dt_to_iso(position.exit_time),
            position.exit_reason,
            position.pnl,
        ))
        self._conn.commit()

    def load_history(self, limit: int = 0) -> list[Position]:
        """Load closed positions from the DB, most recent first.

        Args:
            limit: Max rows to return. 0 = all.

        Returns:
            List of Position objects (oldest first — reversed from the
            DB query so appending to _history gives chronological order).
        """
        assert self._conn is not None, "TradeDatabase not opened"
        query = _SELECT
        if limit > 0:
            query += f" LIMIT {limit}"
        rows = self._conn.execute(query).fetchall()

        positions = []
        for row in rows:
            (figi, ticker, direction, entry_price, quantity, entry_time,
             stop_loss, take_profit, atr, peak_price,
             exit_price, exit_time, exit_reason, pnl) = row

            pos = Position(
                figi=figi,
                ticker=ticker,
                direction=_SENTIMENT_MAP.get(direction, Sentiment.NEUTRAL),
                entry_price=entry_price,
                quantity=quantity,
                entry_time=_iso_to_dt(entry_time) or datetime.now(),
                stop_loss=stop_loss,
                take_profit=take_profit,
                atr=atr,
                peak_price=peak_price,
                state=PositionState.CLOSED,
                is_open=False,
                exit_price=exit_price,
                exit_time=_iso_to_dt(exit_time),
                exit_reason=exit_reason,
                pnl=pnl,
            )
            positions.append(pos)

        positions.reverse()  # oldest first (chronological)
        return positions

    def count(self) -> int:
        assert self._conn is not None, "TradeDatabase not opened"
        row = self._conn.execute(
            "SELECT COUNT(*) FROM closed_positions"
        ).fetchone()
        return row[0] if row else 0
