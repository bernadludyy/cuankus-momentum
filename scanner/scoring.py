from __future__ import annotations

from typing import Any

from config import (
    MIN_DAILY_CHANGE_FOR_LEAKAGE,
    MAX_DAILY_CHANGE_FOR_LEAKAGE,
    MAX_DAILY_CHANGE_FOR_MOMENTUM,
    MIN_ACTIVITY_SPIKE,
    MIN_BREAKOUT_PCT,
    MIN_MOMENTUM_INTERVAL_RETURN_PCT,
)


def _scaled(value: float | None, low: float, high: float, points: float) -> float:
    if value is None:
        return 0.0
    if value <= low:
        return 0.0
    if value >= high:
        return points
    return ((value - low) / (high - low)) * points


def score_leakage(f: dict[str, Any]) -> tuple[float, list[str], bool]:
    score = 0.0
    reasons: list[str] = []

    for label, field, points in (
        ("Volume", "volume_spike", 22),
        ("Value", "value_spike", 20),
        ("Frequency", "frequency_spike", 20),
    ):
        value = f.get(field)
        pts = _scaled(value, 1.0, 3.0, points)
        score += pts
        if value is not None and value >= MIN_ACTIVITY_SPIKE:
            reasons.append(f"{label} {value:.2f}x")

    effort = f.get("effort_result")
    score += _scaled(effort, 2.0, 8.0, 16)
    if effort is not None and effort >= 3:
        reasons.append("Effort besar, harga masih relatif tertahan")

    daily_range = f.get("daily_range_pct")
    if daily_range is not None:
        if daily_range <= 2.5:
            score += 10
            reasons.append(f"Range sempit {daily_range:.2f}%")
        elif daily_range <= 4.0:
            score += 5

    pos = f.get("position_in_range")
    if pos is not None:
        if 0.45 <= pos <= 0.90:
            score += 7
        elif pos > 0.90:
            score += 4

    interval_return = f.get("interval_return_pct")
    if interval_return is not None and -0.30 <= interval_return <= 0.90:
        score += 5

    daily_change = f.get("daily_change_pct")
    anti_falling_knife_pass = True

    if daily_change is not None:
        if daily_change < MIN_DAILY_CHANGE_FOR_LEAKAGE:
            score -= 25
            anti_falling_knife_pass = False
            reasons.append(f"Reject: daily {daily_change:.2f}%")
        elif daily_change > MAX_DAILY_CHANGE_FOR_LEAKAGE:
            score -= 15
            reasons.append(f"Sudah naik {daily_change:.2f}%")

    if interval_return is not None and interval_return < -0.8:
        score -= 20
        anti_falling_knife_pass = False
        reasons.append(f"Reject: interval {interval_return:.2f}%")

    return max(0.0, min(100.0, score)), reasons, anti_falling_knife_pass


def score_momentum(
    f: dict[str, Any],
    leakage_score: float,
) -> tuple[float, list[str], bool]:
    score = leakage_score * 0.35
    reasons: list[str] = []

    activity = f.get("activity_spike")
    score += _scaled(activity, 1.2, 3.0, 25)
    if activity is not None and activity >= MIN_ACTIVITY_SPIKE:
        reasons.append(f"Activity {activity:.2f}x")

    interval_return = f.get("interval_return_pct")
    score += _scaled(interval_return, 0.10, 1.50, 20)
    if (
        interval_return is not None
        and interval_return >= MIN_MOMENTUM_INTERVAL_RETURN_PCT
    ):
        reasons.append(f"Interval +{interval_return:.2f}%")

    breakout = f.get("breakout_pct")
    score += _scaled(breakout, 0.0, 1.0, 15)
    if breakout is not None and breakout >= MIN_BREAKOUT_PCT:
        reasons.append(f"Breakout +{breakout:.2f}%")

    pos = f.get("position_in_range")
    if pos is not None and pos >= 0.75:
        score += 10
        reasons.append("Harga dekat high intraday")

    daily_change = f.get("daily_change_pct")
    not_overextended = True
    if daily_change is not None and daily_change > MAX_DAILY_CHANGE_FOR_MOMENTUM:
        score -= 20
        not_overextended = False
        reasons.append(f"Overextended {daily_change:.2f}%")

    if interval_return is not None and interval_return < 0:
        score -= 15

    return max(0.0, min(100.0, score)), reasons, not_overextended


def classify_signal(
    features: dict[str, Any],
    leakage_threshold: float,
    momentum_threshold: float,
) -> dict[str, Any]:
    leakage_score, leakage_reasons, knife_pass = score_leakage(features)
    momentum_score, momentum_reasons, extension_pass = score_momentum(
        features,
        leakage_score,
    )

    phase = "NONE"
    score = max(leakage_score, momentum_score)
    reasons = leakage_reasons

    if (
        knife_pass
        and extension_pass
        and momentum_score >= momentum_threshold
        and (features.get("breakout_pct") or -999) >= MIN_BREAKOUT_PCT
        and (features.get("interval_return_pct") or -999)
            >= MIN_MOMENTUM_INTERVAL_RETURN_PCT
    ):
        phase = "POWERFUL_MOMENTUM"
        score = momentum_score
        reasons = momentum_reasons

    elif knife_pass and leakage_score >= leakage_threshold:
        phase = "EARLY_LEAKAGE"
        score = leakage_score
        reasons = leakage_reasons

    return {
        "phase": phase,
        "score": round(score, 1),
        "leakage_score": round(leakage_score, 1),
        "momentum_score": round(momentum_score, 1),
        "anti_falling_knife_pass": knife_pass,
        "not_overextended": extension_pass,
        "reasons": reasons,
    }
