"""PTSN-style structural ignition: read-only, private-chat-only watch.

Uses stored five-minute quotes and optionally existing Filter 1/2 microstructure.
No extra ZAPI requests. These are WATCH signals, not guarantees of profit.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from scanner.features import _filter_current_market_day


def _scaled_points(ok: bool, points: int) -> int:
    return points if ok else 0


def build_ptsn_structure(
    snapshots: list[dict[str, Any]],
    features: dict[str, Any],
    micro: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score a recent 30–45 minute base before a valid breakout.

    Baseline excludes the current quote and preceding quote so that the
    breakout does not shift its own reference area. The contiguous base must
    contain at least 6 points, span >=25 minutes, and stay <=2.5% wide.
    """
    rows = _filter_current_market_day(snapshots)
    if len(rows) < 9:
        return None
    base = rows[-9:-2]
    if len(base) < 6 or any(r.get("last") is None for r in base):
        return None
    try:
        times = [datetime.fromisoformat(str(r["ts"])) for r in base]
    except (ValueError, TypeError, KeyError):
        return None
    if any(t.date() != times[0].date() for t in times):
        return None
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    if not gaps or min(gaps) <= 0 or max(gaps) > 12:
        return None
    base_minutes = (times[-1] - times[0]).total_seconds() / 60
    if not 25 <= base_minutes <= 70:
        return None

    prices = [float(r["last"]) for r in base]
    low, high = min(prices), max(prices)
    current = features.get("price")
    if low <= 0 or high <= 0 or current is None:
        return None
    current = float(current)
    compression = (high - low) / low * 100
    breakout = (current - high) / high * 100
    daily = features.get("daily_change_pct")
    interval = features.get("interval_return_pct")
    pos = features.get("position_in_range")
    if compression > 2.5 or not 0.30 <= breakout <= 3.5:
        return None
    if interval is None or interval < 0 or daily is None or daily > 4.0:
        return None
    if pos is None or pos < 0.35:
        return None

    volume = features.get("volume_spike")
    value = features.get("value_spike")
    frequency = features.get("frequency_spike")
    vol_ok = volume is not None and volume >= 1.8
    val_ok = value is not None and value >= 1.5
    freq_ok = frequency is not None and frequency >= 1.5
    if not any((vol_ok, val_ok, freq_ok)):
        return None

    later_low = min(prices[len(prices) // 2:])
    earlier_low = min(prices[:len(prices) // 2])
    higher_low = later_low >= earlier_low
    base_score = (15 if compression <= 1.6 else 10) + 10 + _scaled_points(higher_low, 5)
    breakout_score = 10 + (10 if 0.5 <= breakout <= 2.5 else 5)
    activity_score = _scaled_points(vol_ok, 10) + _scaled_points(val_ok, 8) + _scaled_points(freq_ok, 7)

    micro = micro or {}
    micro_score = micro.get("micro_score")
    aggressive_buy = micro.get("aggressive_buy_ratio")
    spread = micro.get("spread_pct")
    micro_ok = micro_score is not None and micro_score >= 55
    buyer_ok = aggressive_buy is not None and aggressive_buy >= 0.60
    buying_score = _scaled_points(micro_ok, 10) + _scaled_points(buyer_ok, 10)
    spread_score = _scaled_points(spread is not None and 0 <= spread <= 0.8, 5)
    score = base_score + breakout_score + activity_score + buying_score + spread_score
    confirmed = score >= 80 and micro_ok and buyer_ok
    if score < 60:
        return None

    return {
        "ticker": features["ticker"],
        "stage": "CONFIRMED" if confirmed else "WATCH",
        "price": current,
        "structure_score": float(score),
        "base_low": low,
        "base_high": high,
        "base_minutes": round(base_minutes),
        "compression_pct": round(compression, 2),
        "structural_breakout_pct": round(breakout, 2),
        "volume_spike": volume,
        "value_spike": value,
        "frequency_spike": frequency,
        "micro_score": micro_score,
        "aggressive_buy_ratio": aggressive_buy,
        "spread_pct": spread,
    }


def format_ptsn_structure(result: dict[str, Any]) -> str:
    def multiple(value: float | None) -> str:
        return "-" if value is None else f"{value:.2f}x"

    buyer = result.get("aggressive_buy_ratio")
    buy_text = "-" if buyer is None else f"{buyer * 100:.0f}%"
    micro_score = result.get("micro_score")
    micro_text = "-" if micro_score is None else f"{micro_score:.0f}/100"

    return (
        "📩 PTSN STRUCTURAL MOMENTUM | PRIVATE\n"
        f"Ticker: {result['ticker']} | {result['stage']}\n"
        f"Harga: {result['price']:.0f}\n"
        f"Structural Score: {result['structure_score']:.0f}/100\n\n"
        f"Base: {result['base_low']:.0f}–{result['base_high']:.0f} "
        f"({result['base_minutes']} menit)\n"
        f"Compression: {result['compression_pct']:.2f}%\n"
        f"Breakout Base: +{result['structural_breakout_pct']:.2f}%\n"
        f"Volume / Value / Frequency: "
        f"{multiple(result['volume_spike'])} / "
        f"{multiple(result['value_spike'])} / "
        f"{multiple(result['frequency_spike'])}\n"
        f"Micro: {micro_text} | Aggressive Buy: {buy_text}\n"
        "⚠️ WATCH tidak sama dengan konfirmasi entry. "
        "Periksa volume dan risiko false breakout."
    )
