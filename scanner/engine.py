from __future__ import annotations

from datetime import datetime
from typing import Any

from config import (
    LEAKAGE_THRESHOLD,
    MIN_BASELINE_INTERVALS,
    MOMENTUM_THRESHOLD,
    RECENT_SNAPSHOTS,
    SIGNAL_REARM_MINUTES,
)
from scanner.features import build_features
from scanner.scoring import classify_signal
from storage.db import (
    get_recent_snapshots,
    get_signal_state,
    observe_no_signal,
    observe_signal_present,
    save_alert,
    set_signal_anchor,
)


def analyze_ticker(ticker: str) -> dict[str, Any] | None:
    snapshots = get_recent_snapshots(ticker, RECENT_SNAPSHOTS)
    features = build_features(
        snapshots,
        min_baseline_intervals=MIN_BASELINE_INTERVALS,
    )

    if features is None:
        return None

    signal = classify_signal(
        features,
        leakage_threshold=LEAKAGE_THRESHOLD,
        momentum_threshold=MOMENTUM_THRESHOLD,
    )

    return {**features, **signal}


def _phase_rank(phase: str | None) -> int:
    return {
        "EARLY_IGNITION": 1,
        "EARLY_LEAKAGE": 2,
        "PRE_MOMENTUM": 3,
        "POWERFUL_MOMENTUM": 4,
    }.get(phase or "", 0)


def should_alert(result: dict[str, Any]) -> bool:
    """Alert only on a new setup or a genuine phase upgrade.

    Same-phase score changes are silent. Downgrades are silent.
    A ticker is re-armed only after it has spent SIGNAL_REARM_MINUTES
    without a qualifying final signal.
    """
    phase = result.get("phase")
    ticker = result.get("ticker")

    if phase not in {
        "EARLY_IGNITION",
        "PRE_MOMENTUM",
        "EARLY_LEAKAGE",
        "POWERFUL_MOMENTUM",
    }:
        return False

    state = get_signal_state(ticker)
    if not state or not state.get("anchor_phase"):
        return True

    none_since = state.get("none_since")
    if none_since:
        try:
            gap_start = datetime.fromisoformat(none_since)
            gap_minutes = (datetime.now(gap_start.tzinfo) - gap_start).total_seconds() / 60.0
            if gap_minutes >= SIGNAL_REARM_MINUTES:
                return True
        except Exception:
            pass

    return _phase_rank(phase) > _phase_rank(state.get("anchor_phase"))

def mark_alerted(result: dict[str, Any]) -> None:
    save_alert(
        ticker=result["ticker"],
        phase=result["phase"],
        score=result["score"],
        price=result.get("price"),
    )
    set_signal_anchor(
        ticker=result["ticker"],
        phase=result["phase"],
    )



def mark_no_signal(ticker: str) -> None:
    observe_no_signal(ticker)


def mark_signal_present(ticker: str) -> None:
    observe_signal_present(ticker)
