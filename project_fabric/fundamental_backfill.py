"""Resumable full-universe fundamental backfill into Neon."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
import random
import threading
import time
from typing import Any

import requests

from .fundamental_archive import FundamentalArchive, UniverseTicker


BASE_URL = os.getenv("ZPI_BASE_URL", "https://api.zapi.ink/v1/finance:pluang").rstrip("/")
API_KEY = os.getenv("ZPI_API_KEY", "").strip()
ENDPOINTS = ("fundamentals", "financials", "company-profile")
_counter_lock = threading.Lock()
_api_calls = 0


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise RuntimeError("ZPI_API_KEY is required")
    return {"x-api-key": API_KEY, "accept": "application/json"}


def fetch_endpoint(ticker: str, endpoint: str, attempts: int = 4) -> Any:
    global _api_calls
    params: dict[str, Any] = {"code": ticker}
    if endpoint == "financials":
        params["period"] = "both"
    last_error: Exception | None = None
    for attempt in range(attempts):
        with _counter_lock:
            _api_calls += 1
        try:
            response = requests.get(
                f"{BASE_URL}/{endpoint}", headers=_headers(), params=params, timeout=35
            )
            if response.status_code == 404:
                raise RuntimeError("HTTP 404: ticker/endpoint unavailable")
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = min(60.0, float(response.headers.get("Retry-After") or 0))
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.text[:180]}", response=response
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("non-object JSON response")
            return payload
        except Exception as exc:
            last_error = exc
            retryable = not (isinstance(exc, RuntimeError) and "404" in str(exc))
            if not retryable or attempt + 1 >= attempts:
                break
            retry_after = 0.0
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                try:
                    retry_after = float(exc.response.headers.get("Retry-After") or 0)
                except ValueError:
                    retry_after = 0.0
            delay = max(retry_after, min(20.0, (2 ** attempt) + random.random()))
            time.sleep(delay)
    raise RuntimeError(f"{type(last_error).__name__}: {str(last_error)[:300]}")


def collect_one(meta: UniverseTicker) -> tuple[UniverseTicker, dict[str, Any], dict[str, str]]:
    payloads: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for endpoint in ENDPOINTS:
        try:
            payloads[endpoint] = fetch_endpoint(meta.ticker, endpoint)
        except Exception as exc:
            errors[endpoint] = str(exc)[:500]
    return meta, payloads, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 means all eligible tickers")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--stale-days", type=int, default=90)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ticker", action="append", default=[])
    args = parser.parse_args()

    archive = FundamentalArchive()
    archive.migrate()
    universe = archive.universe(stale_days=args.stale_days, force=args.force)
    wanted = {str(t).strip().upper() for t in args.ticker if str(t).strip()}
    if wanted:
        universe = [row for row in universe if row.ticker in wanted]
    if args.limit > 0:
        universe = universe[: args.limit]

    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime("manual-%Y%m%dT%H%M%SZ")
    archive.start_run(run_id, len(universe))
    print(f"[FUNDAMENTAL] run={run_id} tickers={len(universe)} workers={max(1,min(args.workers,8))}")

    complete = partial = failed = completed = 0
    failures: dict[str, Any] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
            futures = {pool.submit(collect_one, meta): meta.ticker for meta in universe}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    meta, payloads, errors = future.result()
                    archive.upsert_ticker(meta, payloads, errors)
                    ok = len(payloads)
                    if ok == len(ENDPOINTS):
                        complete += 1
                    elif ok:
                        partial += 1
                        failures[ticker] = errors
                    else:
                        failed += 1
                        failures[ticker] = errors
                except Exception as exc:
                    failed += 1
                    failures[ticker] = {"archive": f"{type(exc).__name__}: {str(exc)[:500]}"}
                completed += 1
                if completed % 25 == 0 or completed == len(universe):
                    print(f"[FUNDAMENTAL] progress={completed}/{len(universe)} complete={complete} partial={partial} failed={failed} calls={_api_calls}")
    except BaseException:
        archive.finish_run(run_id, completed=completed, complete=complete, partial=partial,
                           failed=failed, api_calls=_api_calls, summary={"failures": failures},
                           status="INTERRUPTED")
        raise

    status = "SUCCESS" if failed == 0 and partial == 0 else ("PARTIAL" if complete or partial else "FAILED")
    summary = {"endpoints": list(ENDPOINTS), "failures": failures,
               "stale_days": args.stale_days, "force": args.force}
    archive.finish_run(run_id, completed=completed, complete=complete, partial=partial,
                       failed=failed, api_calls=_api_calls, summary=summary, status=status)
    print(f"[FUNDAMENTAL] status={status} completed={completed} complete={complete} partial={partial} failed={failed} calls={_api_calls}")
    return 0 if status in {"SUCCESS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

