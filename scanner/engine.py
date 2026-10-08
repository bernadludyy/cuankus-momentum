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


PRIMARY_STAGES = ("EARLY_IGNITION", "PRE_MOMENTUM", "POWERFUL_MOMENTUM")


def _phase_rank(phase: str | None) -> int:
    return {name: i + 1 for i, name in enumerate(PRIMARY_STAGES)}.get(phase or "", 0)


def select_next_primary_phase(
    result: dict[str, Any], state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select the earliest eligible stage, then advance at most one stage.

    Never mislabel an already extended move as ignition: the eligibility
    checks come from the real stage thresholds. Existing anchors survive.
    """
    output = dict(result)
    eligible = [p for p in PRIMARY_STAGES if p in (result.get("eligible_phases") or [])]
    if not eligible:
        return output

    if state is None:
        state = get_signal_state(str(result["ticker"]))
    anchor = (state or {}).get("anchor_phase")
    none_since = (state or {}).get("none_since")
    if none_since:
        try:
            gap_start = datetime.fromisoformat(none_since)
            if (datetime.now(gap_start.tzinfo) - gap_start).total_seconds() / 60 >= SIGNAL_REARM_MINUTES:
                anchor = None
        except (ValueError, TypeError):
            pass

    if anchor in PRIMARY_STAGES:
        index = PRIMARY_STAGES.index(anchor)
        next_stage = PRIMARY_STAGES[index + 1] if index + 1 < len(PRIMARY_STAGES) else None
        if next_stage in eligible:
            phase = next_stage
        else:
            # No phase skipping after an anchor is set. A stronger phase may
            # be pending confirmation of the intermediate stage on a later scan.
            previous_or_lower = [p for p in eligible if _phase_rank(p) <= _phase_rank(anchor)]
            phase = previous_or_lower[-1] if previous_or_lower else "NONE"
    else:
        # First-ever detection: alert the earliest stage actually qualified.
        # Do not fabricate prior stages if scanner missed their entry window.
        phase = eligible[0]

    output["phase"] = phase
    output["pending_stage"] = phase == "NONE" and bool(eligible)
    if phase in PRIMARY_STAGES:
        output["score"] = round(float(result["phase_scores"][phase]), 1)
    if output["pending_stage"]:
        output["reasons"] = list(output.get("reasons") or []) + [
            "Menunggu konfirmasi tahapan momentum berikutnya"
        ]
    return output


def should_alert(result: dict[str, Any]) -> bool:
    """Alert only on a new setup or a genuine phase upgrade.

    Same-phase score changes are silent. Downgrades are silent.
    A ticker is re-armed only after it has spent SIGNAL_REARM_MINUTES
    without a qualifying final signal.
    """
    phase = result.get("phase")
    ticker = result.get("ticker")

    if phase not in PRIMARY_STAGES:
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
