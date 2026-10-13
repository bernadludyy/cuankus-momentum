from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .eod_finalizer import (
    EOD_STATUS_ENDPOINT,
    EOD_STATUS_PARAMS,
    _payload_ticker,
    stock_summary_candle,
    merge_completed_candle,
    psycopg,
)
from .postgres_backend import PostgresTTLCache, configured_database_url
from .universe_intelligence import _cache_key, history_features
from .eod_entry_plan import EOD_PLAN_ENDPOINT, EOD_PLAN_PARAMS, build_eod_entry_plan, build_plan_snapshot


WIB = timezone(timedelta(hours=7))


def rebuild_cached_plans(cache, database_url: str, status: dict) -> dict:
    """Rebuild plans from durable completed EOD and cached history; zero API calls."""
    trade_date = str(status['trade_date'])[:10]
    now = datetime.now(WIB)
    if trade_date > now.date().isoformat() or (trade_date == now.date().isoformat() and now.hour < 17):
        raise RuntimeError('Cannot rebuild an uncompleted EOD session')
    with psycopg.connect(database_url, connect_timeout=8, autocommit=True) as con:
        with con.cursor() as cur:
            cur.execute('SELECT max(trade_date) FROM project_fabric.stock_summary_daily')
            latest_day = cur.fetchone()[0]
            if latest_day and str(latest_day)[:10] > trade_date:
                # A newer collector is finalizing. Never republish yesterday over it.
                return {'status': 'WAITING_FINALIZER', 'trade_date': trade_date, 'count': 0}
            cur.execute('SELECT raw_json FROM project_fabric.stock_summary_daily WHERE trade_date=%s', (trade_date,))
            summaries = [row[0] for row in cur.fetchall()]
            cur.execute("""SELECT value_json FROM project_fabric.cache_entries
                WHERE namespace='shared-v1' AND cache_key LIKE 'idx:stock-history:%%'
                ORDER BY saved_at DESC LIMIT 5000""")
            histories = [row[0] for row in cur.fetchall()]
    by_ticker = {str(row.get('StockCode') or row.get('stockCode') or row.get('ticker') or row.get('code') or '').upper(): row for row in summaries}
    seen = set()
    plans = []
    for history in histories:
        ticker = _payload_ticker(history)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        row = by_ticker.get(ticker)
        candle = stock_summary_candle(row, trade_date=trade_date) if row else None
        if not candle:
            continue
        merged = merge_completed_candle(history, candle)
        features = history_features(ticker, merged, asof=now, completed_through=trade_date)
        plan = build_eod_entry_plan(ticker, merged, features, trade_date=trade_date,
                                    generated_at=now, summary_row=row)
        if plan:
            plans.append(plan)
    result = build_plan_snapshot(plans, trade_date=trade_date, generated_at=now)
    if result['count'] <= 0:
        raise RuntimeError('Cached completed EOD produces no entry plans; keeping existing plans')
    cache.set(_cache_key(EOD_PLAN_ENDPOINT, EOD_PLAN_PARAMS), result)
    return result


def main() -> None:
    database_url = configured_database_url()
    if not database_url:
        raise RuntimeError("PROJECT_FABRIC_DATABASE_URL belum dikonfigurasi.")

    cache = PostgresTTLCache(
        database_url,
        namespace="shared-v1",
        service="cuankus-momentum-eod-plan-bootstrap",
        auto_migrate=False,
    )
    status = cache.get(
        _cache_key(EOD_STATUS_ENDPOINT, EOD_STATUS_PARAMS),
        7 * 24 * 3600,
    )
    if not isinstance(status, dict) or not status.get("trade_date"):
        raise RuntimeError("Belum ada EOD Finalizer status yang bisa dibootstrap.")

    trade_date = str(status["trade_date"])[:10]
    result = rebuild_cached_plans(cache, database_url, status)
    print(
        "[EOD-PLAN-BOOTSTRAP] "
        f"trade_date={result.get('trade_date')} "
        f"status={result.get('status', 'SUCCESS')} "
        f"plans={result.get('count', 0)} zapi_calls=0"
    )


if __name__ == "__main__":
    main()

