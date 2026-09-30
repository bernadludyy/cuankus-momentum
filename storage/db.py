from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import DB_PATH


WIB = timezone(timedelta(hours=7))


def now_wib_iso() -> str:
    return datetime.now(WIB).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = connect()
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            ts TEXT NOT NULL,
            last REAL,
            open REAL,
            high REAL,
            low REAL,
            prev_close REAL,
            volume REAL,
            value REAL,
            frequency REAL,
            bid REAL,
            offer REAL,
            raw_json TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_ts
        ON snapshots(ticker, ts)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            ts TEXT NOT NULL,
            phase TEXT NOT NULL,
            score REAL NOT NULL,
            price REAL
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerts_ticker_phase_ts
        ON alerts(ticker, phase, ts)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_states (
            ticker TEXT PRIMARY KEY,
            anchor_phase TEXT,
            none_since TEXT,
            last_seen_ts TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_cap_cache (
            ticker TEXT PRIMARY KEY,
            market_cap REAL NOT NULL,
            ts TEXT NOT NULL
        )
        """
    )

    # Seed state from historical alerts so an existing signal is not re-alerted
    # immediately after this stateful alert logic is deployed.
    cur.execute(
        """
        INSERT OR IGNORE INTO signal_states (
            ticker, anchor_phase, none_since, last_seen_ts
        )
        SELECT a.ticker, a.phase, NULL, a.ts
        FROM alerts a
        INNER JOIN (
            SELECT ticker, MAX(id) AS max_id
            FROM alerts
            GROUP BY ticker
        ) latest
            ON latest.ticker = a.ticker
           AND latest.max_id = a.id
        """
    )

    con.commit()
    con.close()


def save_snapshot(quote: dict[str, Any], raw: Any = None, ts: str | None = None) -> None:
    if not quote.get("ticker"):
        return

    con = connect()
    con.execute(
        """
        INSERT INTO snapshots (
            ticker, ts, last, open, high, low, prev_close,
            volume, value, frequency, bid, offer, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quote.get("ticker"),
            ts or now_wib_iso(),
            quote.get("last"),
            quote.get("open"),
            quote.get("high"),
            quote.get("low"),
            quote.get("prev_close"),
            quote.get("volume"),
            quote.get("value"),
            quote.get("frequency"),
            quote.get("bid"),
            quote.get("offer"),
            json.dumps(raw, ensure_ascii=False) if raw is not None else None,
        ),
    )
    con.commit()
    con.close()


def get_recent_snapshots(ticker: str, limit: int = 12) -> list[dict[str, Any]]:
    con = connect()
    rows = con.execute(
        """
        SELECT *
        FROM snapshots
        WHERE ticker = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (ticker.upper(), limit),
    ).fetchall()
    con.close()

    data = [dict(row) for row in rows]
    data.reverse()
    return data


def recent_alert_exists(ticker: str, phase: str, cooldown_minutes: int) -> bool:
    cutoff = datetime.now(WIB) - timedelta(minutes=cooldown_minutes)

    con = connect()
    row = con.execute(
        """
        SELECT ts
        FROM alerts
        WHERE ticker = ? AND phase = ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (ticker.upper(), phase),
    ).fetchone()
    con.close()

    if not row:
        return False

    try:
        alert_time = datetime.fromisoformat(row["ts"])
        return alert_time >= cutoff
    except Exception:
        return False


def save_alert(ticker: str, phase: str, score: float, price: float | None) -> None:
    con = connect()
    con.execute(
        """
        INSERT INTO alerts (ticker, ts, phase, score, price)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ticker.upper(), now_wib_iso(), phase, float(score), price),
    )
    con.commit()
    con.close()



def get_signal_state(ticker: str) -> dict[str, Any] | None:
    con = connect()
    row = con.execute(
        """
        SELECT ticker, anchor_phase, none_since, last_seen_ts
        FROM signal_states
        WHERE ticker = ?
        """,
        (ticker.upper(),),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def observe_no_signal(ticker: str) -> None:
    """Start/continue a no-signal gap only for tickers that already have state."""
    now = now_wib_iso()
    con = connect()
    row = con.execute(
        "SELECT none_since FROM signal_states WHERE ticker = ?",
        (ticker.upper(),),
    ).fetchone()

    if row:
        none_since = row["none_since"] or now
        con.execute(
            """
            UPDATE signal_states
            SET none_since = ?, last_seen_ts = ?
            WHERE ticker = ?
            """,
            (none_since, now, ticker.upper()),
        )
        con.commit()

    con.close()


def observe_signal_present(ticker: str) -> None:
    """Signal is still present; cancel a short NONE gap without changing anchor."""
    now = now_wib_iso()
    con = connect()
    row = con.execute(
        "SELECT ticker FROM signal_states WHERE ticker = ?",
        (ticker.upper(),),
    ).fetchone()

    if row:
        con.execute(
            """
            UPDATE signal_states
            SET none_since = NULL, last_seen_ts = ?
            WHERE ticker = ?
            """,
            (now, ticker.upper()),
        )
        con.commit()

    con.close()


def set_signal_anchor(ticker: str, phase: str) -> None:
    now = now_wib_iso()
    con = connect()
    con.execute(
        """
        INSERT INTO signal_states (ticker, anchor_phase, none_since, last_seen_ts)
        VALUES (?, ?, NULL, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            anchor_phase = excluded.anchor_phase,
            none_since = NULL,
            last_seen_ts = excluded.last_seen_ts
        """,
        (ticker.upper(), phase, now),
    )
    con.commit()
    con.close()



def get_cached_market_cap(
    ticker: str,
    max_age_hours: int = 24,
) -> float | None:
    cutoff = datetime.now(WIB) - timedelta(hours=max_age_hours)

    con = connect()
    row = con.execute(
        """
        SELECT market_cap, ts
        FROM market_cap_cache
        WHERE ticker = ?
        """,
        (ticker.upper(),),
    ).fetchone()
    con.close()

    if not row:
        return None

    try:
        cached_at = datetime.fromisoformat(row["ts"])
        if cached_at < cutoff:
            return None
    except Exception:
        return None

    return float(row["market_cap"])


def save_market_cap(ticker: str, market_cap: float) -> None:
    con = connect()
    con.execute(
        """
        INSERT INTO market_cap_cache (ticker, market_cap, ts)
        VALUES (?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            market_cap = excluded.market_cap,
            ts = excluded.ts
        """,
        (ticker.upper(), float(market_cap), now_wib_iso()),
    )
    con.commit()
    con.close()
