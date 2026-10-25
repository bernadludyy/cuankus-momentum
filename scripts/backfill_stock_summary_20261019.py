"""One-day, no-live-scanner historical backfill. Run via manual GitHub Action."""
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

from project_fabric.eod_finalizer import refresh_eod_summary, validate_eod_summary
from project_fabric.market_archive import MarketArchive
from project_fabric.postgres_backend import PostgresTTLCache, configured_database_url

def main():
    target = os.getenv("BACKFILL_TRADE_DATE", "2026-10-19")
    day = date.fromisoformat(target)
    if day.isoformat() != "2026-10-19":
        raise ValueError("This one-off job is restricted to 2026-10-19")
    if day.weekday() >= 5:
        raise ValueError("Weekend is not a trading day")
    archive = MarketArchive(service="cuankus-backfill")
    with archive._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM project_fabric.stock_summary_daily WHERE trade_date=%s", (day,))
        existing = int(cur.fetchone()[0])
    cache = PostgresTTLCache(configured_database_url(), namespace="shared-v1", service="cuankus-backfill", auto_migrate=False)
    payload, saved = refresh_eod_summary(
        target,
        asof=datetime.now(ZoneInfo("Asia/Jakarta")),
        cache=cache,
        archive=archive,
        publish_canonical=False,
    )
    rows = validate_eod_summary(payload, target)
    with archive._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT ticker), count(*) FILTER (WHERE close_price IS NULL OR open_price IS NULL) FROM project_fabric.stock_summary_daily WHERE trade_date=%s", (day,))
        count, unique, missing = map(int, cur.fetchone())
    if count != len(rows) or unique != count or missing:
        raise RuntimeError(f"VERIFY_FAILED source={len(rows)} db={count} unique={unique} missing={missing}")
    print(f"BACKFILL_SUCCESS date={target} rows={count} unique={unique} missing_ohlc={missing} archived={saved} existing_before={existing}")

if __name__ == "__main__":
    main()
