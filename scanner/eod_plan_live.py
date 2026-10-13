from __future__ import annotations

from datetime import datetime, timedelta
import os
from typing import Any

from project_fabric.eod_entry_plan import (
    EOD_PLAN_ENDPOINT,
    EOD_PLAN_PARAMS,
    EOD_PLAN_TTL_SECONDS,
)
from project_fabric.postgres_backend import (
    PostgresTTLCache,
    configured_database_url,
)
from project_fabric.universe_intelligence import _cache_key
from scanner.market_hours import WIB
from storage.db import ensure_reversal_watch


def load_eod_entry_plans(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(WIB)).astimezone(WIB)
    database_url = configured_database_url()
    if not database_url:
        return {"trade_date": None, "plans": [], "count": 0}

    cache = PostgresTTLCache(
        database_url,
        namespace=os.getenv("PROJECT_FABRIC_CACHE_NAMESPACE", "shared-v1"),
        service="cuankus-momentum",
        auto_migrate=False,
    )
    payload = cache.get(
        _cache_key(EOD_PLAN_ENDPOINT, EOD_PLAN_PARAMS),
        EOD_PLAN_TTL_SECONDS,
    )
    if not isinstance(payload, dict):
        return {"trade_date": None, "plans": [], "count": 0}

    trade_date = str(payload.get("trade_date") or "")[:10]
    if not trade_date:
        return {"trade_date": None, "plans": [], "count": 0}

    try:
        plan_day = datetime.fromisoformat(trade_date).date()
    except ValueError:
        return {"trade_date": None, "plans": [], "count": 0}

    age_days = (current.date() - plan_day).days
    if age_days < 1 or age_days > 4:
        return {"trade_date": trade_date, "plans": [], "count": 0}

    plans = [
        item
        for item in (payload.get("plans") or [])
        if isinstance(item, dict) and item.get("ticker")
    ]
    return {
        "trade_date": trade_date,
        "generated_at": payload.get("generated_at"),
        "count": len(plans),
        "plans": plans,
    }


def _distance_to_zone_pct(price: float, plan: dict[str, Any]) -> float:
    lower = float(plan["entry_zone_lower"])
    upper = float(plan["entry_zone_upper"])
    if lower <= price <= upper:
        return 0.0
    if price > upper:
        return (price - upper) / price * 100.0
    return (lower - price) / max(price, 1.0) * 100.0


def seed_eod_entry_watches(
    quotes: list[dict[str, Any]],
    *,
    max_price: float,
    max_plans: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(WIB)).astimezone(WIB)
    snapshot = load_eod_entry_plans(now=current)
    plans = snapshot.get("plans") or []
    if not plans:
        return {
            "trade_date": snapshot.get("trade_date"),
            "available": 0,
            "seeded": 0,
            "near_zone": 0,
        }

    price_by_ticker = {
        str(row.get("ticker") or "").upper(): row.get("last")
        for row in quotes
        if row.get("ticker") and row.get("last") is not None
    }

    eligible: list[tuple[float, float, dict[str, Any], float]] = []
    near_zone = 0
    for plan in plans[: max(1, int(max_plans))]:
        ticker = str(plan.get("ticker") or "").upper()
        raw_price = price_by_ticker.get(ticker)
        if raw_price is None:
            continue
        price = float(raw_price)
        if price <= 0 or price >= float(max_price):
            continue

        invalidation = float(plan.get("invalidation_price") or 0.0)
        if price < invalidation:
            continue

        distance = _distance_to_zone_pct(price, plan)
        if distance <= 1.5:
            near_zone += 1

        # Seed all eligible plans; live microstructure still remains capped and
        # only starts after zone touch. Score and distance are logged only.
        eligible.append(
            (
                distance,
                -float(plan.get("eod_score") or 0.0),
                plan,
                price,
            )
        )

    eligible.sort(key=lambda item: (item[0], item[1], item[2]["ticker"]))
    seeded = 0
    seed_ts = current.replace(hour=8, minute=59, second=0, microsecond=0).isoformat()

    for _distance, _neg_score, plan, _price in eligible:
        ticker = str(plan["ticker"]).upper()
        if ensure_reversal_watch(
            ticker=ticker,
            source_lane=f"EOD_{plan.get('setup_type')}",
            source_phase="EOD_PLAN",
            target="private" if ticker == "PTSN" else "all",
            alert_price=float(plan.get("eod_close") or plan["entry_zone_upper"]),
            pullback_lower=float(plan["entry_zone_lower"]),
            pullback_upper=float(plan["entry_zone_upper"]),
            reversal_trigger=float(plan["reversal_trigger"]),
            invalidation_price=float(plan["invalidation_price"]),
            alert_ts=seed_ts,
        ):
            seeded += 1

    return {
        "trade_date": snapshot.get("trade_date"),
        "available": len(eligible),
        "seeded": seeded,
        "near_zone": near_zone,
        "plans": [item[2] for item in eligible],
    }


def prioritize_reversal_watches(
    watches: list[dict[str, Any]],
    price_by_ticker: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    def rank(watch: dict[str, Any]):
        ticker = str(watch.get("ticker") or "").upper()
        price_raw = price_by_ticker.get(ticker)
        if price_raw is None:
            return (9, 999.0, 0.0, ticker)
        price = float(price_raw)
        state = str(watch.get("state") or "")
        lane = str(watch.get("source_lane") or "")
        lower = float(watch.get("pullback_lower") or 0.0)
        upper = float(watch.get("pullback_upper") or 0.0)

        in_zone = lower <= price <= upper
        eod = lane.startswith("EOD_")
        if state == "PULLBACK_ACTIVE":
            bucket = 0
        elif eod and in_zone:
            bucket = 1
        elif in_zone:
            bucket = 2
        elif eod:
            bucket = 3
        else:
            bucket = 4

        distance = 0.0 if in_zone else (
            (price - upper) / max(price, 1.0) * 100.0
            if price > upper
            else (lower - price) / max(price, 1.0) * 100.0
        )
        return (bucket, distance, -float(watch.get("last_score") or 0.0), ticker)

    ranked = [
        watch
        for watch in watches
        if price_by_ticker.get(str(watch.get("ticker") or "").upper()) is not None
    ]
    ranked.sort(key=rank)
    return ranked[: max(1, int(limit))]
