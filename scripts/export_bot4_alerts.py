from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row


WIB = ZoneInfo("Asia/Jakarta")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export successful Bot 4 Telegram alert deliveries."
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
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

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "service": "cuankus-momentum",
        "timezone": "Asia/Jakarta",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "generated_at": datetime.now(WIB).isoformat(),
        "row_count": len(rows),
        "rows": rows,
    }
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Exported {len(rows)} successful Telegram alert deliveries")


if __name__ == "__main__":
    main()
