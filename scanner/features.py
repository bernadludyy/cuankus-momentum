from __future__ import annotations

from datetime import datetime
import math
from statistics import median
from typing import Any

from scanner.market_hours import is_idx_market_window


def _filter_current_market_day(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only snapshots from the current WIB trading day and regular session.

    This prevents overnight/manual zero-activity snapshots from becoming the
    baseline for the next market session.
    """
    if not snapshots:
        return []

    try:
        current_dt = datetime.fromisoformat(snapshots[-1]["ts"])
        current_date = current_dt.date()
    except Exception:
        return snapshots

    filtered: list[dict[str, Any]] = []
    for item in snapshots:
        try:
            dt = datetime.fromisoformat(item["ts"])
        except Exception:
            continue

        if dt.date() == current_date and is_idx_market_window(dt):
            filtered.append(item)

    return filtered


def _safe_pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return ((new / old) - 1.0) * 100.0


def _minutes_between(a: str, b: str) -> float:
    try:
        ta = datetime.fromisoformat(a)
        tb = datetime.fromisoformat(b)
        minutes = (tb - ta).total_seconds() / 60.0
        return max(minutes, 0.01)
    except Exception:
        return 1.0


def _rate_delta(curr: dict[str, Any], prev: dict[str, Any], field: str) -> float | None:
    a = prev.get(field)
    b = curr.get(field)

    if a is None or b is None:
        return None

    delta = float(b) - float(a)
    if delta < 0:
        return None

    minutes = _minutes_between(prev["ts"], curr["ts"])
    if minutes > 15:
        return None
    return delta / minutes


def _spike(
    current_rate: float | None,
    baseline_rates: list[float],
    min_baseline_intervals: int,
) -> float | None:
    clean = [x for x in baseline_rates if x is not None and x >= 0]
    if current_rate is None or len(clean) < min_baseline_intervals:
        return None

    base = median(clean)
    if base <= 0:
        return 1.0 if current_rate <= 0 else 5.0

    return current_rate / base


def price_volume_context(features: dict, window_return_pct: float | None) -> dict:
    """Research-only price/volume context; never adds points or gates alerts.

    Inspired by Lee & Swaminathan (2000), DOI 10.1111/0022-1082.00280.
    Intraday thresholds are hypotheses, not a replication of monthly momentum.
    """
    def finite(value):
        try:
            return value is not None and math.isfinite(float(value))
        except (ValueError, TypeError, OverflowError):
            return False

    volume = features.get('volume_spike')
    interval = features.get('interval_return_pct')
    daily = features.get('daily_change_pct')
    state = 'DATA_BELUM_CUKUP'
    if all(finite(v) for v in (volume, interval, daily, window_return_pct)) and float(volume) >= 0:
        volume, interval, daily, window_return_pct = map(float, (volume, interval, daily, window_return_pct))
        if window_return_pct >= 5 or daily >= 8:
            state = 'SUDAH_JAUH_NAIK'
        elif interval < 0 and volume >= 1.5:
            state = 'TURUN_DENGAN_VOLUME'
        elif interval > 0 and window_return_pct > 0 and volume >= 1.5:
            state = 'MENGUAT_DENGAN_VOLUME'
        elif interval > 0 and volume < 1:
            state = 'NAIK_VOLUME_LEMAH'
        elif volume >= 1.5:
            state = 'VOLUME_TANPA_KENAIKAN'
        else:
            state = 'BELUM_TERKONFIRMASI'
    return {
        'price_volume_context': state,
        'price_volume_window_return_pct': window_return_pct if finite(window_return_pct) else None,
        'price_volume_note': 'Riset: volume per menit vs median interval sebelumnya pada hari yang sama; bukan RVOL jam-sama lintas hari.',
    }


def format_price_volume_context(value: str | None) -> str:
    """Human-readable Indonesian label for Telegram/log output."""
    return {
        "MENGUAT_DENGAN_VOLUME": "Menguat + volume",
        "NAIK_VOLUME_LEMAH": "Naik, volume lemah",
        "TURUN_DENGAN_VOLUME": "Turun + volume",
        "VOLUME_TANPA_KENAIKAN": "Volume naik, harga tertahan",
        "SUDAH_JAUH_NAIK": "Sudah jauh naik",
        "BELUM_TERKONFIRMASI": "Belum terkonfirmasi",
        "DATA_BELUM_CUKUP": "Data belum cukup",
    }.get(value or "", "Data belum cukup")


def build_features(
    snapshots: list[dict[str, Any]],
    min_baseline_intervals: int = 4,
) -> dict[str, Any] | None:
    snapshots = _filter_current_market_day(snapshots)

    if len(snapshots) < min_baseline_intervals + 2:
        return None

    current = snapshots[-1]
    previous = snapshots[-2]

    interval_return_pct = _safe_pct(current.get("last"), previous.get("last"))
    daily_change_pct = _safe_pct(current.get("last"), current.get("prev_close"))

    daily_range_pct = None
    if (
        current.get("high") is not None
        and current.get("low") is not None
        and current.get("prev_close")
    ):
        daily_range_pct = (
            (float(current["high"]) - float(current["low"]))
            / float(current["prev_close"])
            * 100.0
        )

    position_in_range = None
    if (
        current.get("last") is not None
        and current.get("high") is not None
        and current.get("low") is not None
        and float(current["high"]) > float(current["low"])
    ):
        position_in_range = (
            (float(current["last"]) - float(current["low"]))
            / (float(current["high"]) - float(current["low"]))
        )

    fields = ("volume", "value", "frequency")
    current_rates: dict[str, float | None] = {}
    baseline_rates: dict[str, list[float]] = {f: [] for f in fields}

    for field in fields:
        current_rates[field] = _rate_delta(current, previous, field)

        for i in range(1, len(snapshots) - 1):
            rate = _rate_delta(snapshots[i], snapshots[i - 1], field)
            if rate is not None:
                baseline_rates[field].append(rate)

    volume_spike = _spike(
        current_rates["volume"],
        baseline_rates["volume"],
        min_baseline_intervals,
    )
    value_spike = _spike(
        current_rates["value"],
        baseline_rates["value"],
        min_baseline_intervals,
    )
    frequency_spike = _spike(
        current_rates["frequency"],
        baseline_rates["frequency"],
        min_baseline_intervals,
    )

    prior_prices = [
        float(x["last"])
        for x in snapshots[:-1]
        if x.get("last") is not None
    ]

    breakout_pct = None
    if prior_prices and current.get("last") is not None:
        prior_high = max(prior_prices[-5:])
        breakout_pct = _safe_pct(float(current["last"]), prior_high)

    spikes = [
        x for x in (volume_spike, value_spike, frequency_spike)
        if x is not None
    ]
    activity_spike = sum(spikes) / len(spikes) if spikes else None

    effort_result = None
    if activity_spike is not None and interval_return_pct is not None:
        effort_result = activity_spike / max(abs(interval_return_pct), 0.15)

    features = {
        "ticker": current["ticker"],
        "price": current.get("last"),
        "daily_change_pct": daily_change_pct,
        "interval_return_pct": interval_return_pct,
        "daily_range_pct": daily_range_pct,
        "position_in_range": position_in_range,
        "volume_spike": volume_spike,
        "value_spike": value_spike,
        "frequency_spike": frequency_spike,
        "activity_spike": activity_spike,
        "effort_result": effort_result,
        "breakout_pct": breakout_pct,
        "volume_rate": current_rates["volume"],
        "value_rate": current_rates["value"],
        "frequency_rate": current_rates["frequency"],
    }

    # Use only a contiguous, valid trailing window. Sparse/malformed timestamps,
    # resets and synthetic zero-baseline spikes cannot confirm research context.
    window = snapshots[-6:]
    window_return = None
    try:
        times = [datetime.fromisoformat(row['ts']) for row in window]
        prices = [float(row['last']) for row in window]
        valid = len(window) == 6 and all(math.isfinite(p) and p > 0 for p in prices)
        valid = valid and all(0 < (b-a).total_seconds() <= 900 for a,b in zip(times,times[1:]))
        volumes = [float(row['volume']) for row in window]
        valid = valid and all(math.isfinite(v) and v >= 0 for v in volumes)
        valid = valid and all(b >= a for a,b in zip(volumes,volumes[1:]))
        if valid:
            window_return = _safe_pct(prices[-1], prices[0])
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    baseline = baseline_rates['volume']
    valid_baseline = (len(baseline) >= min_baseline_intervals and
                      all(math.isfinite(x) and x >= 0 for x in baseline) and median(baseline) > 0)
    context_features = features if valid_baseline else {**features, 'volume_spike': None}
    features.update(price_volume_context(context_features, window_return))
    return features
