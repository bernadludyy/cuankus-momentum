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
        CREATE TABLE IF NOT EXISTS screener_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            screener TEXT NOT NULL,
            ticker TEXT NOT NULL,
            ts TEXT NOT NULL,
            score REAL NOT NULL,
            price REAL
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_screener_alerts_key_ts
        ON screener_alerts(screener, ticker, ts)
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_filter_cache (
            ticker TEXT PRIMARY KEY,
            volume_ma_short REAL,
            volume_ma_long REAL,
            volume_ma_ratio REAL,
            value_ma REAL,
            history_days INTEGER NOT NULL,
            enough_history INTEGER NOT NULL,
            swing_low REAL,
            swing_high REAL,
            swing_low_date TEXT,
            swing_high_date TEXT,
            golden_pocket_lower REAL,
            golden_pocket_upper REAL,
            golden_pocket_lookback INTEGER,
            swing_low_5d REAL,
            swing_high_5d REAL,
            swing_low_date_5d TEXT,
            swing_high_date_5d TEXT,
            golden_pocket_lower_5d REAL,
            golden_pocket_upper_5d REAL,
            golden_pocket_lookback_5d INTEGER,
            ts TEXT NOT NULL
        )
        """
    )

    # Backward-compatible cache migration for Golden Pocket fields.
    daily_filter_columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(daily_filter_cache)").fetchall()
    }
    for column_name, column_type in (
        ("swing_low", "REAL"),
        ("swing_high", "REAL"),
        ("swing_low_date", "TEXT"),
        ("swing_high_date", "TEXT"),
        ("golden_pocket_lower", "REAL"),
        ("golden_pocket_upper", "REAL"),
        ("golden_pocket_lookback", "INTEGER"),
        ("swing_low_5d", "REAL"),
        ("swing_high_5d", "REAL"),
        ("swing_low_date_5d", "TEXT"),
        ("swing_high_date_5d", "TEXT"),
        ("golden_pocket_lower_5d", "REAL"),
        ("golden_pocket_upper_5d", "REAL"),
        ("golden_pocket_lookback_5d", "INTEGER"),
    ):
        if column_name not in daily_filter_columns:
            cur.execute(
                f"ALTER TABLE daily_filter_cache "
                f"ADD COLUMN {column_name} {column_type}"
            )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shareholder_cache (
            ticker TEXT PRIMARY KEY,
            holder_count INTEGER,
            report_published_at TEXT,
            announcement_id TEXT,
            source_url TEXT,
            status TEXT NOT NULL,
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



def recent_screener_alert_exists(
    screener: str,
    ticker: str,
    cooldown_minutes: int,
) -> bool:
    cutoff = datetime.now(WIB) - timedelta(minutes=cooldown_minutes)
    con = connect()
    row = con.execute(
        """
        SELECT ts
        FROM screener_alerts
        WHERE screener = ? AND ticker = ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (str(screener), ticker.upper()),
    ).fetchone()
    con.close()

    if not row:
        return False
    try:
        return datetime.fromisoformat(row["ts"]) >= cutoff
    except Exception:
        return False


def save_screener_alert(
    screener: str,
    ticker: str,
    score: float,
    price: float | None,
) -> None:
    con = connect()
    con.execute(
        """
        INSERT INTO screener_alerts (screener, ticker, ts, score, price)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(screener),
            ticker.upper(),
            now_wib_iso(),
            float(score),
            price,
        ),
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



def get_cached_daily_filter(
    ticker: str,
    max_age_hours: int = 18,
) -> dict[str, Any] | None:
    cutoff = datetime.now(WIB) - timedelta(hours=max_age_hours)

    con = connect()
    row = con.execute(
        """
        SELECT
            volume_ma_short,
            volume_ma_long,
            volume_ma_ratio,
            value_ma,
            history_days,
            enough_history,
            swing_low,
            swing_high,
            swing_low_date,
            swing_high_date,
            golden_pocket_lower,
            golden_pocket_upper,
            golden_pocket_lookback,
            swing_low_5d,
            swing_high_5d,
            swing_low_date_5d,
            swing_high_date_5d,
            golden_pocket_lower_5d,
            golden_pocket_upper_5d,
            golden_pocket_lookback_5d,
            ts
        FROM daily_filter_cache
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

    return {
        "volume_ma_short": row["volume_ma_short"],
        "volume_ma_long": row["volume_ma_long"],
        "volume_ma_ratio": row["volume_ma_ratio"],
        "value_ma": row["value_ma"],
        "history_days": int(row["history_days"]),
        "enough_history": bool(row["enough_history"]),
        "swing_low": row["swing_low"],
        "swing_high": row["swing_high"],
        "swing_low_date": row["swing_low_date"],
        "swing_high_date": row["swing_high_date"],
        "golden_pocket_lower": row["golden_pocket_lower"],
        "golden_pocket_upper": row["golden_pocket_upper"],
        "golden_pocket_lookback": row["golden_pocket_lookback"],
        "swing_low_5d": row["swing_low_5d"],
        "swing_high_5d": row["swing_high_5d"],
        "swing_low_date_5d": row["swing_low_date_5d"],
        "swing_high_date_5d": row["swing_high_date_5d"],
        "golden_pocket_lower_5d": row["golden_pocket_lower_5d"],
        "golden_pocket_upper_5d": row["golden_pocket_upper_5d"],
        "golden_pocket_lookback_5d": row[
            "golden_pocket_lookback_5d"
        ],
    }


def save_daily_filter(ticker: str, metrics: dict[str, Any]) -> None:
    con = connect()
    con.execute(
        """
        INSERT INTO daily_filter_cache (
            ticker,
            volume_ma_short,
            volume_ma_long,
            volume_ma_ratio,
            value_ma,
            history_days,
            enough_history,
            swing_low,
            swing_high,
            swing_low_date,
            swing_high_date,
            golden_pocket_lower,
            golden_pocket_upper,
            golden_pocket_lookback,
            swing_low_5d,
            swing_high_5d,
            swing_low_date_5d,
            swing_high_date_5d,
            golden_pocket_lower_5d,
            golden_pocket_upper_5d,
            golden_pocket_lookback_5d,
            ts
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(ticker) DO UPDATE SET
            volume_ma_short = excluded.volume_ma_short,
            volume_ma_long = excluded.volume_ma_long,
            volume_ma_ratio = excluded.volume_ma_ratio,
            value_ma = excluded.value_ma,
            history_days = excluded.history_days,
            enough_history = excluded.enough_history,
            swing_low = excluded.swing_low,
            swing_high = excluded.swing_high,
            swing_low_date = excluded.swing_low_date,
            swing_high_date = excluded.swing_high_date,
            golden_pocket_lower = excluded.golden_pocket_lower,
            golden_pocket_upper = excluded.golden_pocket_upper,
            golden_pocket_lookback = excluded.golden_pocket_lookback,
            swing_low_5d = excluded.swing_low_5d,
            swing_high_5d = excluded.swing_high_5d,
            swing_low_date_5d = excluded.swing_low_date_5d,
            swing_high_date_5d = excluded.swing_high_date_5d,
            golden_pocket_lower_5d = excluded.golden_pocket_lower_5d,
            golden_pocket_upper_5d = excluded.golden_pocket_upper_5d,
            golden_pocket_lookback_5d = excluded.golden_pocket_lookback_5d,
            ts = excluded.ts
        """,
        (
            ticker.upper(),
            metrics.get("volume_ma_short"),
            metrics.get("volume_ma_long"),
            metrics.get("volume_ma_ratio"),
            metrics.get("value_ma"),
            int(metrics.get("history_days") or 0),
            1 if metrics.get("enough_history") else 0,
            metrics.get("swing_low"),
            metrics.get("swing_high"),
            metrics.get("swing_low_date"),
            metrics.get("swing_high_date"),
            metrics.get("golden_pocket_lower"),
            metrics.get("golden_pocket_upper"),
            (
                int(metrics.get("golden_pocket_lookback"))
                if metrics.get("golden_pocket_lookback") is not None
                else None
            ),
            metrics.get("swing_low_5d"),
            metrics.get("swing_high_5d"),
            metrics.get("swing_low_date_5d"),
            metrics.get("swing_high_date_5d"),
            metrics.get("golden_pocket_lower_5d"),
            metrics.get("golden_pocket_upper_5d"),
            (
                int(metrics.get("golden_pocket_lookback_5d"))
                if metrics.get("golden_pocket_lookback_5d") is not None
                else None
            ),
            now_wib_iso(),
        ),
    )
    con.commit()
    con.close()


def get_cached_shareholder_filter(
    ticker: str,
    success_max_age_hours: int = 168,
    failure_max_age_hours: int = 6,
) -> dict[str, Any] | None:
    con = connect()
    row = con.execute(
        """
        SELECT
            holder_count,
            report_published_at,
            announcement_id,
            source_url,
            status,
            ts
        FROM shareholder_cache
        WHERE ticker = ?
        """,
        (ticker.upper(),),
    ).fetchone()
    con.close()

    if not row:
        return None

    status = str(row["status"] or "UNKNOWN")
    max_age_hours = (
        success_max_age_hours
        if status == "OK" and row["holder_count"] is not None
        else failure_max_age_hours
    )
    cutoff = datetime.now(WIB) - timedelta(hours=max_age_hours)

    try:
        cached_at = datetime.fromisoformat(row["ts"])
        if cached_at < cutoff:
            return None
    except Exception:
        return None

    return {
        "holder_count": (
            int(row["holder_count"])
            if row["holder_count"] is not None
            else None
        ),
        "report_published_at": row["report_published_at"],
        "announcement_id": row["announcement_id"],
        "source_url": row["source_url"],
        "status": status,
    }


def save_shareholder_filter(
    ticker: str,
    holder_count: int | None,
    report_published_at: str | None = None,
    announcement_id: str | None = None,
    source_url: str | None = None,
    status: str = "OK",
) -> None:
    con = connect()
    con.execute(
        """
        INSERT INTO shareholder_cache (
            ticker,
            holder_count,
            report_published_at,
            announcement_id,
            source_url,
            status,
            ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            holder_count = excluded.holder_count,
            report_published_at = excluded.report_published_at,
            announcement_id = excluded.announcement_id,
            source_url = excluded.source_url,
            status = excluded.status,
            ts = excluded.ts
        """,
        (
            ticker.upper(),
            int(holder_count) if holder_count is not None else None,
            report_published_at,
            announcement_id,
            source_url,
            status,
            now_wib_iso(),
        ),
    )
    con.commit()
    con.close()
