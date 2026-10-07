from __future__ import annotations

from typing import Any


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _scaled(value: Any, low: float, high: float, points: float) -> float:
    if value is None:
        return 0.0
    value = float(value)
    if value <= low:
        return 0.0
    if value >= high:
        return float(points)
    return ((value - low) / (high - low)) * float(points)


def score_flow_pre(result: dict[str, Any]) -> float:
    """Cheap second-screener rank using data already fetched for every ticker.

    No extra API call is required. This deliberately differs from the legacy
    leakage/momentum score by emphasizing foreign flow + activity before the
    six expensive microstructure slots are allocated.
    """
    if not result.get("anti_falling_knife_pass", True):
        return 0.0
    if not result.get("not_overextended", True):
        return 0.0

    foreign = _clamp(float(result.get("foreign_score") or 0.0)) / 100.0 * 40.0
    activity = _scaled(result.get("activity_spike"), 1.0, 3.0, 25.0)
    volume = _scaled(result.get("volume_spike"), 1.0, 6.0, 20.0)
    position = _scaled(result.get("position_in_range"), 0.40, 0.90, 10.0)
    interval = _scaled(result.get("interval_return_pct"), 0.0, 1.50, 5.0)

    return round(_clamp(foreign + activity + volume + position + interval), 1)


def score_flow_final(result: dict[str, Any]) -> tuple[float, str, list[str]]:
    """Flow-confluence score after the same microstructure fetch used by Bot 4.

    Weighting:
      foreign flow       30
      microstructure     25
      aggressive buy     15
      bid dominance      10
      volume spike       10
      activity spike     10
    """
    foreign_score = _clamp(float(result.get("foreign_score") or 0.0))
    micro_score = _clamp(float(result.get("micro_score") or 0.0))

    score = (
        foreign_score / 100.0 * 30.0
        + micro_score / 100.0 * 25.0
        + _scaled(result.get("aggressive_buy_ratio"), 0.50, 0.80, 15.0)
        + _scaled(result.get("bid_percent"), 48.0, 68.0, 10.0)
        + _scaled(result.get("volume_spike"), 1.50, 6.0, 10.0)
        + _scaled(result.get("activity_spike"), 1.20, 3.0, 10.0)
    )
    score = round(_clamp(score), 1)

    if score >= 85.0:
        status = "STRONG FLOW CONFLUENCE"
    elif score >= 75.0:
        status = "FLOW CONFLUENCE"
    elif score >= 65.0:
        status = "FLOW WATCH"
    else:
        status = "NONE"

    reasons: list[str] = []
    if foreign_score >= 75:
        reasons.append(f"Foreign flow kuat {foreign_score:.1f}/100")
    if micro_score >= 65:
        reasons.append(f"Microstructure kuat {micro_score:.1f}/100")

    buy_ratio = result.get("aggressive_buy_ratio")
    if buy_ratio is not None and float(buy_ratio) >= 0.65:
        reasons.append(f"Aggressive buy {float(buy_ratio) * 100:.0f}%")

    bid = result.get("bid_percent")
    if bid is not None and float(bid) >= 58:
        reasons.append(f"Bid dominance {float(bid):.0f}%")

    vol = result.get("volume_spike")
    if vol is not None and float(vol) >= 3.0:
        reasons.append(f"Volume spike {float(vol):.2f}x")

    return score, status, reasons
