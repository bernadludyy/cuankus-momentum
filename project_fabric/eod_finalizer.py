"""Post-close EOD collector and finalizer for Universe Intelligence.

The scanner intentionally excludes today's partial candle intraday. After the IDX
session is closed, this job combines the latest cached stock-summary OHLCV row
with already-cached stock-history, recalculates Universe Intelligence, and writes
the result back to the shared Neon cache and permanent daily archive. An exact
dated stock-summary request refreshes EOD; stock-history remains cache-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import math
import re
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from .postgres_backend import PostgresTTLCache, configured_database_url
from .data_gateway import DataGateway, CachePolicy
from .market_archive import MarketArchive
from .universe_intelligence import (
    HISTORY_ENDPOINT,
    SUMMARY_ENDPOINT,
    SUMMARY_PARAMS,
    _cache_key,
    history_features,
    summary_snapshot,
)
from .eod_entry_plan import (
    EOD_PLAN_ENDPOINT,
    EOD_PLAN_PARAMS,
    build_eod_entry_plan,
    build_plan_snapshot,
)


WIB = timezone(timedelta(hours=7))
NAMESPACE = os.getenv("PROJECT_FABRIC_CACHE_NAMESPACE", "shared-v1").strip() or "shared-v1"
EOD_STATUS_ENDPOINT = "universe:intelligence:eod-status:v1"
EOD_STATUS_PARAMS = {"scope": "IDX"}


def _number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, "") or isinstance(value, bool):
            continue
        try:
            number = float(str(value).replace(",", ""))
            if math.isfinite(number):
                return number
        except (TypeError, ValueError):
            continue
    return None


def _ticker(row: dict[str, Any]) -> str:
    return str(
        row.get("StockCode")
        or row.get("stockCode")
        or row.get("ticker")
        or row.get("Code")
        or row.get("code")
        or ""
    ).strip().upper()


def _items(payload: Any) -> list[dict[str, Any]]:
    current = payload
    for _ in range(6):
        if isinstance(current, list):
            return [x for x in current if isinstance(x, dict)]
        if not isinstance(current, dict):
            return []
        child = current.get("items")
        if not isinstance(child, (list, dict)):
            child = current.get("data")
        if isinstance(child, (list, dict)):
            current = child
            continue
        return []
    return []


def _payload_ticker(payload: Any) -> str:
    current = payload
    for _ in range(6):
        if isinstance(current, dict):
            code = (
                current.get("code")
                or current.get("Code")
                or current.get("ticker")
                or current.get("StockCode")
            )
            if code:
                return str(code).strip().upper()
            child = current.get("data")
            if isinstance(child, dict):
                current = child
                continue
        break
    rows = _items(payload)
    return _ticker(rows[0]) if rows else ""


def stock_summary_candle(
    row: dict[str, Any],
    *,
    trade_date: str,
) -> dict[str, Any] | None:
    row_date = str(
        row.get("Date")
        or row.get("date")
        or row.get("TradingDate")
        or ""
    )[:10]
    if row_date != trade_date:
        return None

    opening = _number(row, "OpenPrice", "Open", "open")
    high = _number(row, "High", "high")
    low = _number(row, "Low", "low")
    close = _number(row, "Close", "close")
    volume = _number(row, "Volume", "volume")
    value = _number(row, "Value", "value")

    if None in (opening, high, low, close, volume):
        return None
    if opening <= 0 or low <= 0 or high < low or not low <= close <= high or not low <= opening <= high or volume < 0:
        return None

    return {
        "Date": trade_date,
        "Open": opening,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
        "Value": value,
        "_source": "stock-summary-eod",
    }


def merge_completed_candle(
    history_payload: Any,
    candle: dict[str, Any],
    *,
    max_sessions: int = 120,
) -> dict[str, Any]:
    rows = [dict(x) for x in _items(history_payload)]
    target = str(candle["Date"])[:10]

    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        stamp = str(row.get("date") or row.get("Date") or "")[:10]
        if len(stamp) == 10 and stamp <= target:
            by_date[stamp] = row

    # EOD stock-summary is authoritative for the just-completed target session.
    by_date[target] = dict(candle)
    ordered = [by_date[key] for key in sorted(by_date)]
    if max_sessions > 0:
        ordered = ordered[-int(max_sessions):]
    return {"data": ordered}


def _choose_summary_payload(
    candidates: list[tuple[Any, datetime]],
    *,
    trade_date: str,
) -> tuple[Any, datetime, dict[str, dict[str, Any]]]:
    best: tuple[Any, datetime, dict[str, dict[str, Any]]] | None = None
    best_count = -1

    for payload, saved_at in candidates:
        rows = _items(payload)
        mapped: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = _ticker(row)
            if not code:
                continue
            candle = stock_summary_candle(row, trade_date=trade_date)
            if candle is not None:
                mapped[code] = row
        if len(mapped) > best_count:
            best_count = len(mapped)
            best = (payload, saved_at, mapped)

    if best is None or best_count <= 0:
        raise RuntimeError(
            f"Tidak ada stock-summary cache dengan completed date {trade_date}."
        )
    return best


class EODSourceNotReady(ValueError):
    """A response is stale, mixed-date or incomplete, never a successful EOD."""


def validate_eod_summary(payload: Any, trade_date: str) -> list[dict[str, Any]]:
    rows = _items(payload)
    if not rows:
        raise EODSourceNotReady("Stock-summary EOD kosong")
    seen: set[str] = set()
    for row in rows:
        stamp = str(row.get("Date") or row.get("date") or row.get("TradingDate") or "")[:10]
        code = _ticker(row)
        if stamp != trade_date:
            raise EODSourceNotReady(f"Target {trade_date}, source row date {stamp or 'missing'}")
        if not re.fullmatch(r"[A-Z0-9]{1,15}", code) or code in seen:
            raise EODSourceNotReady("Ticker invalid/duplikat; snapshot tidak lengkap")
        seen.add(code)
    current = payload
    for _ in range(6):
        if not isinstance(current, dict):
            break
        for key in ("recordsFiltered", "recordsTotal", "total"):
            if key in current:
                try:
                    total = int(current[key])
                except (TypeError, ValueError):
                    raise EODSourceNotReady("Invalid stock-summary total") from None
                if total != len(rows):
                    raise EODSourceNotReady(f"Incomplete EOD snapshot: {len(rows)}/{total}")
        current = current.get("data")
    if len(rows) >= 5000:
        raise EODSourceNotReady("Pagination required; truncated EOD cannot be published")
    return rows


def refresh_eod_summary(trade_date: str, *, asof: datetime, cache: Any,
                        gateway: Any = None, archive: Any = None) -> tuple[Any, int]:
    api_key = os.getenv("ZPI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ZPI_API_KEY belum dikonfigurasi untuk EOD refresh")
    gateway = gateway or DataGateway(service="cuankus-momentum-eod")
    archive = archive or MarketArchive(service="cuankus-momentum-eod")
    params = {"date": trade_date, "length": 5000, "start": 0}
    payload = gateway.get_json(
        endpoint="idx:stock-summary",
        url=os.getenv("ZPI_IDX_BASE_URL", "https://api.zapi.ink/v1/finance:idx").rstrip("/") + "/stock-summary",
        headers={"x-api-key": api_key, "accept": "application/json"},
        params=params, timeout=30, policy=CachePolicy(),
        validate=lambda value: bool(validate_eod_summary(value, trade_date)),
    )
    # Validation precedes all writes. A stale fallback can never be relabelled today.
    validate_eod_summary(payload, trade_date)
    stored = archive.save_summary(payload, completed_through=trade_date, asof=asof)
    cache.set(DataGateway._cache_key("idx:stock-summary", params), payload)
    # Publish canonical raw key for the existing cache-only Telegram readers.
    cache.set(DataGateway._cache_key("idx:stock-summary", {"length": 5000, "start": 0}), payload)
    return payload, stored


def finalize_eod(
    *,
    asof: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    now = (asof or datetime.now(WIB)).astimezone(WIB)
    trade_date = now.date().isoformat()

    if now.weekday() >= 5 and not force:
        return {
            "status": "SKIPPED_WEEKEND",
            "trade_date": trade_date,
            "finalized": 0,
        }
    if (now.hour, now.minute) < (17, 0):
        return {"status": "SKIPPED_BEFORE_EOD", "trade_date": trade_date, "finalized": 0}

    database_url = configured_database_url()
    if not database_url:
        raise RuntimeError("PROJECT_FABRIC_DATABASE_URL belum dikonfigurasi.")
    if psycopg is None:
        raise RuntimeError("psycopg tidak tersedia.")

    cache = PostgresTTLCache(database_url, namespace=NAMESPACE,
                             service="cuankus-momentum-eod", auto_migrate=False)
    previous = cache.get(_cache_key(EOD_STATUS_ENDPOINT, EOD_STATUS_PARAMS), 7 * 86400)
    if not force and isinstance(previous, dict) and previous.get("trade_date") == trade_date and previous.get("status") == "SUCCESS" and previous.get("archived_summary_rows", 0) > 0:
        return dict(previous, status="ALREADY_FINALIZED", zapi_calls=0)

    gateway = DataGateway(service="cuankus-momentum-eod")
    try:
        summary_payload, archived_rows = refresh_eod_summary(
            trade_date, asof=now, cache=cache, gateway=gateway,
        )
    except EODSourceNotReady as exc:
        status = {"status": "WAITING_SOURCE", "trade_date": trade_date,
                  "finalized_at": now.isoformat(timespec="seconds"),
                  "finalized": 0, "reason": str(exc),
                  "zapi_calls": gateway.usage()["totals"]["upstream_attempts"]}
        cache.set(_cache_key(EOD_STATUS_ENDPOINT, EOD_STATUS_PARAMS), status)
        return status
    summary_saved_at = now
    _, _, summary_rows = _choose_summary_payload([(summary_payload, now)], trade_date=trade_date)

    with psycopg.connect(
        database_url,
        connect_timeout=8,
        autocommit=True,
    ) as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT value_json, saved_at
                FROM project_fabric.cache_entries
                WHERE namespace = %s
                  AND cache_key LIKE 'idx:stock-history:%%'
                ORDER BY saved_at DESC
                LIMIT 5000
                """,
                (NAMESPACE,),
            )
            history_candidates = list(cur.fetchall())

    # Keep only the newest raw history payload for each ticker.
    latest_history: dict[str, tuple[Any, datetime]] = {}
    for payload, saved_at in history_candidates:
        code = _payload_ticker(payload)
        if not code or code in latest_history:
            continue
        latest_history[code] = (payload, saved_at)

    eod_summary = summary_snapshot(
        summary_payload,
        observed_at=now.isoformat(timespec="seconds"),
    )
    eod_summary["source"] = "idx:stock-summary:eod-finalized"
    eod_summary["eod_finalized_date"] = trade_date
    cache.set(_cache_key(SUMMARY_ENDPOINT, SUMMARY_PARAMS), eod_summary)

    finalized = 0
    skipped_no_summary = 0
    skipped_short_history = 0
    failed = 0
    failures: list[str] = []
    entry_plans: list[dict[str, Any]] = []

    for ticker, (history_payload, history_saved_at) in latest_history.items():
        summary_row = summary_rows.get(ticker)
        if summary_row is None:
            skipped_no_summary += 1
            continue

        candle = stock_summary_candle(summary_row, trade_date=trade_date)
        if candle is None:
            skipped_no_summary += 1
            continue

        try:
            merged = merge_completed_candle(
                history_payload,
                candle,
                max_sessions=120,
            )
            calculated = history_features(
                ticker,
                merged,
                asof=now,
                completed_through=trade_date,
                source="idx:stock-history+stock-summary-eod",
            )
            if (
                calculated.get("history_sessions", 0) < 30
                or calculated.get("history_last_date") != trade_date
            ):
                skipped_short_history += 1
                continue

            plan = build_eod_entry_plan(
                ticker,
                merged,
                calculated,
                trade_date=trade_date,
                generated_at=now,
                summary_row=summary_row,
            )
            if plan is not None:
                entry_plans.append(plan)
                calculated["eod_entry_plan"] = plan

            calculated.update(
                {
                    "eod_finalized": True,
                    "eod_finalized_date": trade_date,
                    "eod_finalized_at": now.isoformat(timespec="seconds"),
                    "eod_summary_saved_at": summary_saved_at.isoformat(),
                    "base_history_saved_at": history_saved_at.isoformat(),
                }
            )
            cache.set(
                _cache_key(HISTORY_ENDPOINT, {"ticker": ticker}),
                calculated,
            )
            finalized += 1
        except Exception as exc:
            failed += 1
            if len(failures) < 20:
                failures.append(
                    f"{ticker}:{type(exc).__name__}:{str(exc)[:120]}"
                )

    plan_snapshot = build_plan_snapshot(
        entry_plans,
        trade_date=trade_date,
        generated_at=now,
    )
    cache.set(
        _cache_key(EOD_PLAN_ENDPOINT, EOD_PLAN_PARAMS),
        plan_snapshot,
    )

    status = {
        "schema_version": 1,
        "status": "SUCCESS" if finalized > 0 and failed == 0 else "PARTIAL",
        "trade_date": trade_date,
        "finalized_at": now.isoformat(timespec="seconds"),
        "source": "cached-stock-history+exact-date-stock-summary-eod",
        "zapi_calls": gateway.usage()["totals"]["upstream_attempts"],
        "archived_summary_rows": archived_rows,
        "summary_rows": len(summary_rows),
        "history_payloads": len(latest_history),
        "finalized": finalized,
        "entry_plan_count": plan_snapshot.get("count", 0),
        "skipped_no_summary": skipped_no_summary,
        "skipped_short_history": skipped_short_history,
        "failed": failed,
        "failures": failures,
    }
    cache.set(_cache_key(EOD_STATUS_ENDPOINT, EOD_STATUS_PARAMS), status)
    return status


def main() -> None:
    force = os.getenv("EOD_FINALIZER_FORCE", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    result = finalize_eod(force=force)
    print(
        "[EOD-FINALIZER] "
        f"status={result.get('status')} "
        f"trade_date={result.get('trade_date')} "
        f"finalized={result.get('finalized', 0)} "
        f"failed={result.get('failed', 0)} "
        f"plans={result.get('entry_plan_count', 0)} "
        f"zapi_calls={result.get('zapi_calls', 0)}"
    )
    if result.get("failures"):
        for item in result["failures"]:
            print(f"[EOD-FINALIZER-FAIL] {item}")
    if result.get("reason"):
        print(f"[EOD-FINALIZER] {result['reason']}")
    if result.get("status") == "WAITING_SOURCE" or (result.get("status") == "PARTIAL" and result.get("finalized", 0) == 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

