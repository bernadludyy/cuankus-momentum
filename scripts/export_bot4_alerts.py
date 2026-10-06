from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from scanner.market_data import fetch_stock_history


WIB = ZoneInfo("Asia/Jakarta")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export successful Bot 4 Telegram alert deliveries."
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scanner-db", default="")
    return parser.parse_args()


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def json_ready(row: dict) -> dict:
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.astimezone(WIB).isoformat()
    result["signal_payload"] = result.get("signal_payload") or {}
    result["delivery_metadata"] = result.get("delivery_metadata") or {}
    return result


def scanner_alerts(path: str, start_date: date, end_date: date) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, ticker, ts, phase, score, price
            FROM alerts
            WHERE substr(ts, 1, 10) >= ?
              AND substr(ts, 1, 10) <= ?
            ORDER BY ts ASC, id ASC
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    return [dict(row) for row in rows]


def main() -> None:
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if end_date < start_date:
        raise ValueError("end-date must be on or after start-date")

    start_wib = datetime.combine(start_date, time.min, tzinfo=WIB)
    end_exclusive_wib = datetime.combine(
        end_date + timedelta(days=1),
        time.min,
        tzinfo=WIB,
    )

    database_url = os.getenv("PROJECT_FABRIC_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("PROJECT_FABRIC_DATABASE_URL is required")

    query = """
        SELECT
            d.event_id AS delivery_event_id,
            d.run_id AS delivery_run_id,
            d.channel,
            d.status AS delivery_status,
            d.occurred_at AS delivered_at,
            d.metadata AS delivery_metadata,
            s.event_id AS signal_event_id,
            s.run_id AS signal_run_id,
            s.signal_type,
            s.entity AS ticker,
            s.score,
            s.status AS signal_status,
            s.occurred_at AS signal_created_at,
            s.payload AS signal_payload
        FROM project_fabric.delivery_events AS d
        JOIN project_fabric.signal_events AS s
          ON s.event_id = d.signal_event_id
        WHERE d.service = %s
          AND s.service = %s
          AND lower(d.channel) = 'telegram'
          AND lower(d.status) = 'success'
          AND d.occurred_at >= %s
          AND d.occurred_at < %s
        ORDER BY d.occurred_at ASC, s.entity ASC
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    "cuankus-momentum",
                    "cuankus-momentum",
                    start_wib,
                    end_exclusive_wib,
                ),
            )
            rows = [json_ready(row) for row in cursor.fetchall()]

    local_alerts = scanner_alerts(args.scanner_db, start_date, end_date)
    tickers = sorted(
        {
            str(item.get("ticker") or "").upper()
            for item in [*rows, *local_alerts]
            if item.get("ticker")
        }
    )
    stock_history: dict[str, object] = {}
    for ticker in tickers:
        try:
            stock_history[ticker] = fetch_stock_history(ticker, length=140)
            print(f"Fetched stock history for {ticker}")
        except Exception as exc:
            stock_history[ticker] = {
                "error": f"{type(exc).__name__}: {exc}"
            }
            print(f"[WARN] stock history {ticker}: {exc}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "service": "cuankus-momentum",
        "timezone": "Asia/Jakarta",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "generated_at": datetime.now(WIB).isoformat(),
        "row_count": len(rows),
        "scanner_alert_count": len(local_alerts),
        "rows": rows,
        "scanner_alerts": local_alerts,
        "stock_history": stock_history,
    }
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Exported {len(rows)} successful delivery events, "
        f"{len(local_alerts)} scanner alerts, and "
        f"{len(stock_history)} daily histories"
    )


if __name__ == "__main__":
    main()
