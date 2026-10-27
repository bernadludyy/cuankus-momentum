"""Candidate-only MTF confirmation from cached scanner snapshots.

The intraday bars here are sampled-price bars, not exchange-native candles.
They deliberately reuse the SQLite snapshots already collected by Bot 4 and
never make a market-data request. Daily context comes from completed EOD
history already used by the daily filter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from statistics import median
from typing import Any

from config import MTF_CONFIRMATION_ENABLED, MTF_MIN_MOVE_PCT


WIB = timezone(timedelta(hours=7))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WIB)
    return parsed.astimezone(WIB)


def build_sampled_bars(
    snapshots: list[dict[str, Any]],
    timeframe_minutes: int,
) -> list[dict[str, Any]]:
    """Aggregate cached quote samples into deterministic local-time buckets."""
    timeframe = max(1, int(timeframe_minutes))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in snapshots:
        if not isinstance(raw, dict):
            continue
        ts = _timestamp(raw.get("ts"))
        price = _number(raw.get("last"))
        if ts is None or price is None or price <= 0:
            continue
        key = ts.isoformat()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "ts": ts,
                "price": price,
                "volume": _number(raw.get("volume")),
            }
        )
    normalized.sort(key=lambda row: row["ts"])
    if normalized:
        # Never bridge yesterday's close into today's intraday trend. During
        # the first part of a new session the confirmation must warm up and
        # report insufficient data instead of borrowing prior-day bars.
        latest_session = normalized[-1]["ts"].date()
        normalized = [
            row for row in normalized if row["ts"].date() == latest_session
        ]

    prior: dict[str, Any] | None = None
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    for row in normalized:
        ts = row["ts"]
        session = ts.date().isoformat()
        volume_delta = None
        if prior is not None and prior["ts"].date() == ts.date():
            current_volume = row.get("volume")
            prior_volume = prior.get("volume")
            if current_volume is not None and prior_volume is not None:
                delta = current_volume - prior_volume
                if delta >= 0:
                    volume_delta = delta
        prior = row

        minute_of_day = ts.hour * 60 + ts.minute
        bucket_index = minute_of_day // timeframe
        bucket_key = (session, bucket_index)
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "ts": ts,
                "open": row["price"],
                "high": row["price"],
                "low": row["price"],
                "close": row["price"],
                "volume_delta": 0.0,
                "samples": 0,
            }
            order.append(bucket_key)
        bar = buckets[bucket_key]
        bar["ts"] = ts
        bar["high"] = max(float(bar["high"]), row["price"])
        bar["low"] = min(float(bar["low"]), row["price"])
        bar["close"] = row["price"]
        bar["samples"] += 1
        if volume_delta is not None:
            bar["volume_delta"] += volume_delta

    return [buckets[key] for key in order]


def _return_pct(current: float, previous: float) -> float:
    return (current / previous - 1.0) * 100.0 if previous > 0 else 0.0


def _trend(
    bars: list[dict[str, Any]],
    *,
    minimum_bars: int,
) -> tuple[str, float | None]:
    if len(bars) < minimum_bars:
        return "INSUFFICIENT", None
    window = bars[-max(minimum_bars, 5):]
    closes = [float(row["close"]) for row in window]
    change = _return_pct(closes[-1], closes[0])
    mean_close = sum(closes) / len(closes)
    if change >= MTF_MIN_MOVE_PCT and closes[-1] >= mean_close:
        return "UP", round(change, 2)
    if change <= -MTF_MIN_MOVE_PCT and closes[-1] <= mean_close:
        return "DOWN", round(change, 2)
    return "SIDEWAYS", round(change, 2)


def _five_minute_trigger(
    bars: list[dict[str, Any]],
) -> tuple[str, float | None, bool]:
    if len(bars) < 5:
        return "INSUFFICIENT", None, False
    window = bars[-6:]
    closes = [float(row["close"]) for row in window]
    change = _return_pct(closes[-1], closes[0])
    prior_high = max(closes[-4:-1])
    prior_volumes = [
        float(row["volume_delta"])
        for row in window[:-1]
        if _number(row.get("volume_delta")) not in (None, 0.0)
    ]
    latest_volume = _number(window[-1].get("volume_delta")) or 0.0
    volume_ok = (
        len(prior_volumes) >= 2
        and latest_volume >= median(prior_volumes)
    )
    trigger = closes[-1] > prior_high and volume_ok
    if trigger:
        state = "TRIGGER"
    elif closes[-1] >= closes[-2]:
        state = "WAIT"
    else:
        state = "WEAK"
    return state, round(change, 2), trigger


def evaluate_mtf_confirmation(
    result: dict[str, Any],
    snapshots: list[dict[str, Any]],
    *,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Return fail-closed MTF context without performing I/O."""
    active = MTF_CONFIRMATION_ENABLED if enabled is None else bool(enabled)
    base = {
        "mtf_enabled": active,
        "mtf_source": "CACHED_SCANNER_SNAPSHOTS",
    }
    if not active:
        return {**base, "mtf_status": "DISABLED"}

    bars_5m = build_sampled_bars(snapshots, 5)
    bars_15m = build_sampled_bars(snapshots, 15)
    bars_1h = build_sampled_bars(snapshots, 60)

    price = _number(result.get("price"))
    sma20 = _number(result.get("daily_sma20"))
    sma20_prev = _number(result.get("daily_sma20_prev"))
    if price is None or sma20 is None or sma20_prev is None:
        daily = "INSUFFICIENT"
    elif price > sma20 and sma20 >= sma20_prev:
        daily = "UP"
    elif price < sma20 and sma20 <= sma20_prev:
        daily = "DOWN"
    else:
        daily = "SIDEWAYS"

    trend_1h, return_1h = _trend(bars_1h, minimum_bars=3)
    trend_15m, return_15m = _trend(bars_15m, minimum_bars=4)
    trigger_5m, return_5m, has_trigger = _five_minute_trigger(bars_5m)

    states = (daily, trend_1h, trend_15m, trigger_5m)
    if "INSUFFICIENT" in states:
        status = "INSUFFICIENT"
    elif "DOWN" in (daily, trend_1h, trend_15m) or trigger_5m == "WEAK":
        status = "CONFLICT"
    elif (
        daily == "UP"
        and trend_1h == "UP"
        and trend_15m == "UP"
        and has_trigger
    ):
        status = "ALIGNED"
    else:
        status = "WAIT_TRIGGER"

    return {
        **base,
        "mtf_status": status,
        "mtf_daily": daily,
        "mtf_1h": trend_1h,
        "mtf_15m": trend_15m,
        "mtf_5m": trigger_5m,
        "mtf_1h_return_pct": return_1h,
        "mtf_15m_return_pct": return_15m,
        "mtf_5m_return_pct": return_5m,
        "mtf_1h_bars": len(bars_1h),
        "mtf_15m_bars": len(bars_15m),
        "mtf_5m_bars": len(bars_5m),
    }


def apply_mtf_entry_gate(result: dict[str, Any]) -> dict[str, Any]:
    """Hold only actionable Solo Mode entries until MTF is truly aligned."""
    output = dict(result)
    if not output.get("mtf_enabled") or not output.get("solo_actionable"):
        return output
    if output.get("mtf_status") == "ALIGNED":
        return output

    status = str(output.get("mtf_status") or "INSUFFICIENT")
    reason = {
        "WAIT_TRIGGER": "MTF menunggu trigger 5 menit",
        "CONFLICT": "MTF belum selaras",
        "INSUFFICIENT": "Data MTF belum cukup",
    }.get(status, "MTF belum mengonfirmasi entry")
    failures = list(output.get("solo_gate_failures") or [])
    if reason not in failures:
        failures.append(reason)
    output.update(
        {
            "solo_delivery_allowed": False,
            "solo_actionable": False,
            "solo_status": "MTF_WAIT",
            "solo_gate_failures": failures,
        }
    )
    return output
