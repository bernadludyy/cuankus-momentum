from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from config import (
    BIG_PRINT_MIN_LOT,
    MICRO_MIN_CONFIRM_SCORE,
    IGNITION_THRESHOLD,
    IGNITION_MIN_VOLUME_SPIKE,
    IGNITION_MIN_MICRO_SCORE,
    IGNITION_MIN_AGGRESSIVE_BUY,
    IGNITION_MIN_INTERVAL_RETURN_PCT,
    IGNITION_MAX_INTERVAL_RETURN_PCT,
    IGNITION_MAX_DAILY_CHANGE_PCT,
    MOMENTUM_THRESHOLD,
    LEAKAGE_THRESHOLD,
    MIN_BREAKOUT_PCT,
    MIN_MOMENTUM_INTERVAL_RETURN_PCT,
    PRE_MOMENTUM_THRESHOLD,
    PRE_MOMENTUM_MIN_MICRO_SCORE,
    PRE_MOMENTUM_MIN_AGGRESSIVE_BUY,
    PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT,
    PRE_MOMENTUM_MIN_BREAKOUT_PCT,
    PRE_MOMENTUM_MIN_POSITION_IN_RANGE,
    MIN_ACTIVITY_SPIKE,
    RUNNING_TRADE_MIN_LOT,
    RUNNING_TRADE_MAX_AGE_MINUTES,
    FILTER2_LEAKAGE_THRESHOLD,
    FILTER2_MOMENTUM_THRESHOLD,
    FILTER2_PRE_MOMENTUM_THRESHOLD,
    FILTER2_MICRO_MIN_CONFIRM_SCORE,
    FILTER2_PRE_MOMENTUM_MIN_MICRO_SCORE,
    FILTER2_PRE_MOMENTUM_MIN_AGGRESSIVE_BUY,
    FILTER2_PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT,
    FILTER2_PRE_MOMENTUM_MIN_BREAKOUT_PCT,
    FILTER2_PRE_MOMENTUM_MIN_POSITION_IN_RANGE,
    FILTER2_IGNITION_THRESHOLD,
    FILTER2_IGNITION_MIN_VOLUME_SPIKE,
    FILTER2_IGNITION_MIN_MICRO_SCORE,
    FILTER2_IGNITION_MIN_AGGRESSIVE_BUY,
    FILTER2_MIN_POSITION_IN_RANGE,
)
from scanner.market_data import fetch_orderbook, fetch_running_trades
from scanner.market_hours import WIB


def _unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(3):
        if (
            isinstance(current, dict)
            and "project" in current
            and isinstance(current.get("data"), (dict, list))
        ):
            current = current["data"]
            continue
        break
    return current


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _gte(value: float | None, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _scaled(value: float | None, low: float, high: float, points: float) -> float:
    if value is None:
        return 0.0
    if value <= low:
        return 0.0
    if value >= high:
        return points
    return ((value - low) / (high - low)) * points


def analyze_orderbook(payload: Any) -> dict[str, Any]:
    data = _unwrap(payload)
    if not isinstance(data, dict):
        return {}

    bids = data.get("bids") or []
    asks = data.get("asks") or []

    top_bids = [x for x in bids[:5] if isinstance(x, dict)]
    top_asks = [x for x in asks[:5] if isinstance(x, dict)]

    bid_lots = sum(_num(x.get("lots")) or 0 for x in top_bids)
    ask_lots = sum(_num(x.get("lots")) or 0 for x in top_asks)
    total_depth = bid_lots + ask_lots

    bid_percent = _num(data.get("bidPercent"))
    ask_percent = _num(data.get("askPercent"))

    if bid_percent is None and total_depth > 0:
        bid_percent = bid_lots / total_depth * 100.0
    if ask_percent is None and bid_percent is not None:
        ask_percent = 100.0 - bid_percent

    best_bid = _num(data.get("bestBid"))
    best_ask = _num(data.get("bestAsk"))

    spread_pct = None
    if best_bid and best_ask and (best_bid + best_ask) > 0:
        mid = (best_bid + best_ask) / 2.0
        spread_pct = (best_ask - best_bid) / mid * 100.0

    depth_buy_ratio = None
    if total_depth > 0:
        depth_buy_ratio = bid_lots / total_depth

    return {
        "bid_percent": bid_percent,
        "ask_percent": ask_percent,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_pct": spread_pct,
        "top5_bid_lots": bid_lots,
        "top5_ask_lots": ask_lots,
        "depth_buy_ratio": depth_buy_ratio,
    }


def _trade_age_minutes(time_text: Any, now: datetime | None = None) -> float | None:
    if time_text is None:
        return None
    text = str(time_text).strip()
    if not text:
        return None
    try:
        trade_clock = datetime.strptime(text, "%H:%M:%S").time()
    except ValueError:
        return None
    now = now or datetime.now(WIB)
    now = now.replace(tzinfo=WIB) if now.tzinfo is None else now.astimezone(WIB)
    trade_dt = datetime.combine(now.date(), trade_clock, tzinfo=WIB)
    if trade_dt > now + timedelta(minutes=2):
        trade_dt -= timedelta(days=1)
    return (now - trade_dt).total_seconds() / 60.0


def analyze_running_trades(payload: Any, max_age_minutes: int = RUNNING_TRADE_MAX_AGE_MINUTES) -> dict[str, Any]:
    data = _unwrap(payload)
    if not isinstance(data, dict):
        return {}
    items = [x for x in (data.get("items") or []) if isinstance(x, dict)]
    fresh_items, ages = [], []
    stale_count = unknown_time_count = 0
    now = datetime.now(WIB)
    for item in items:
        age = _trade_age_minutes(item.get("time"), now=now)
        if age is None or age < -2:
            unknown_time_count += 1
            continue
        if age > max_age_minutes:
            stale_count += 1
            continue
        fresh_items.append(item)
        ages.append(max(age, 0.0))

    buy_lots = sell_lots = big_buy_lots = big_sell_lots = 0.0
    for item in fresh_items:
        lots = _num(item.get("lots")) or 0.0
        action = str(item.get("action") or "").upper()
        if action == "BUY":
            buy_lots += lots
            if lots >= BIG_PRINT_MIN_LOT:
                big_buy_lots += lots
        elif action == "SELL":
            sell_lots += lots
            if lots >= BIG_PRINT_MIN_LOT:
                big_sell_lots += lots

    total_lots = buy_lots + sell_lots
    big_total = big_buy_lots + big_sell_lots
    return {
        "trade_count": len(fresh_items),
        "raw_trade_count": len(items),
        "stale_trade_count": stale_count,
        "unknown_trade_time_count": unknown_time_count,
        "latest_trade_age_minutes": min(ages) if ages else None,
        "buy_lots": buy_lots,
        "sell_lots": sell_lots,
        "net_aggressive_lots": buy_lots - sell_lots,
        "aggressive_buy_ratio": buy_lots / total_lots if total_lots > 0 else None,
        "big_buy_lots": big_buy_lots,
        "big_sell_lots": big_sell_lots,
        "big_buy_ratio": big_buy_lots / big_total if big_total > 0 else None,
    }


def score_microstructure(book: dict[str, Any], trades: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    bid_percent = book.get("bid_percent")
    score += _scaled(bid_percent, 48, 68, 25)
    if bid_percent is not None and bid_percent >= 55:
        reasons.append(f"Bid dominance {bid_percent:.0f}%")

    depth_ratio = book.get("depth_buy_ratio")
    score += _scaled(depth_ratio, 0.48, 0.68, 15)
    if depth_ratio is not None and depth_ratio >= 0.58:
        reasons.append(f"Top-5 bid depth {depth_ratio * 100:.0f}%")

    spread = book.get("spread_pct")
    if spread is not None:
        if spread <= 0.8:
            score += 5
        elif spread > 2.5:
            score -= 5

    buy_ratio = trades.get("aggressive_buy_ratio")
    score += _scaled(buy_ratio, 0.48, 0.72, 35)
    if buy_ratio is not None and buy_ratio >= 0.58:
        reasons.append(f"Aggressive buy {buy_ratio * 100:.0f}%")

    big_buy_ratio = trades.get("big_buy_ratio")
    score += _scaled(big_buy_ratio, 0.45, 0.75, 15)
    if big_buy_ratio is not None and big_buy_ratio >= 0.60:
        reasons.append(f"Big-print buy {big_buy_ratio * 100:.0f}%")

    net_lots = trades.get("net_aggressive_lots")
    if net_lots is not None and net_lots > 0:
        score += 5

    score = max(0.0, min(100.0, score))
    return score, reasons


def fetch_microstructure(ticker: str) -> dict[str, Any]:
    orderbook_raw = fetch_orderbook(ticker)
    trades_raw = fetch_running_trades(
        ticker,
        min_lot=RUNNING_TRADE_MIN_LOT,
    )

    book = analyze_orderbook(orderbook_raw)
    trades = analyze_running_trades(trades_raw)
    micro_score, reasons = score_microstructure(book, trades)

    return {
        **book,
        **trades,
        "micro_score": round(micro_score, 1),
        "micro_reasons": reasons,
    }


def score_ignition(
    result: dict[str, Any],
    micro: dict[str, Any],
) -> tuple[float, list[str]]:
    """Score abnormal activity before a full momentum breakout."""
    score = 0.0
    reasons: list[str] = []

    volume_spike = result.get("volume_spike")
    micro_score = float(micro.get("micro_score") or 0.0)
    buy_ratio = micro.get("aggressive_buy_ratio")
    interval_return = result.get("interval_return_pct")

    score += _scaled(volume_spike, 2.0, 6.0, 35)
    score += _scaled(micro_score, 50.0, 80.0, 30)
    score += _scaled(buy_ratio, 0.55, 0.80, 25)

    if interval_return is not None and -0.20 <= interval_return <= 0.80:
        score += 10

    if volume_spike is not None and volume_spike >= IGNITION_MIN_VOLUME_SPIKE:
        reasons.append(f"Early volume ignition {volume_spike:.2f}x")
    if buy_ratio is not None and buy_ratio >= IGNITION_MIN_AGGRESSIVE_BUY:
        reasons.append(f"Buyer mulai agresif {buy_ratio * 100:.0f}%")

    return max(0.0, min(100.0, score)), reasons


def apply_micro_confirmation(result: dict[str, Any], micro: dict[str, Any]) -> dict[str, Any]:
    output = {**result, **micro}

    micro_score = float(micro.get("micro_score") or 0.0)
    adjustment = (micro_score - 50.0) * 0.25

    leakage = max(0.0, min(100.0, float(result["leakage_score"]) + adjustment))
    momentum = max(0.0, min(100.0, float(result["momentum_score"]) + adjustment))

    phase = "NONE"
    reasons = list(result.get("reasons") or [])
    reasons.extend(micro.get("micro_reasons") or [])

    ignition_score, ignition_reasons = score_ignition(result, micro)
    ignition_interval = result.get("interval_return_pct")
    ignition_daily = result.get("daily_change_pct")
    ignition_volume = result.get("volume_spike")
    ignition_buy_ratio = micro.get("aggressive_buy_ratio")
    micro_confirmed = micro_score >= MICRO_MIN_CONFIRM_SCORE

    if (
        micro_confirmed
        and momentum >= MOMENTUM_THRESHOLD
        and _gte(result.get("breakout_pct"), MIN_BREAKOUT_PCT)
        and _gte(result.get("interval_return_pct"), MIN_MOMENTUM_INTERVAL_RETURN_PCT)
        and result.get("anti_falling_knife_pass")
        and result.get("not_overextended")
    ):
        phase = "POWERFUL_MOMENTUM"
        score = momentum
    elif (
        micro_score >= PRE_MOMENTUM_MIN_MICRO_SCORE
        and momentum >= PRE_MOMENTUM_THRESHOLD
        and (result.get("activity_spike") or 0) >= MIN_ACTIVITY_SPIKE
        and _gte(result.get("interval_return_pct"), PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT)
        and _gte(result.get("breakout_pct"), PRE_MOMENTUM_MIN_BREAKOUT_PCT)
        and (result.get("position_in_range") or 0)
            >= PRE_MOMENTUM_MIN_POSITION_IN_RANGE
        and (micro.get("aggressive_buy_ratio") or 0)
            >= PRE_MOMENTUM_MIN_AGGRESSIVE_BUY
        and result.get("anti_falling_knife_pass")
        and result.get("not_overextended")
    ):
        phase = "PRE_MOMENTUM"
        score = momentum
        reasons.append("Momentum mulai terbentuk, belum full confirmation")
    elif (
        micro_confirmed
        and leakage >= LEAKAGE_THRESHOLD
        and result.get("anti_falling_knife_pass")
    ):
        phase = "EARLY_LEAKAGE"
        score = leakage
    elif (
        ignition_score >= IGNITION_THRESHOLD
        and ignition_volume is not None
        and float(ignition_volume) >= IGNITION_MIN_VOLUME_SPIKE
        and micro_score >= IGNITION_MIN_MICRO_SCORE
        and ignition_buy_ratio is not None
        and float(ignition_buy_ratio) >= IGNITION_MIN_AGGRESSIVE_BUY
        and ignition_interval is not None
        and IGNITION_MIN_INTERVAL_RETURN_PCT
            <= float(ignition_interval)
            <= IGNITION_MAX_INTERVAL_RETURN_PCT
        and (
            ignition_daily is None
            or float(ignition_daily) <= IGNITION_MAX_DAILY_CHANGE_PCT
        )
        and result.get("anti_falling_knife_pass")
        and result.get("not_overextended")
    ):
        phase = "EARLY_IGNITION"
        score = ignition_score
        reasons.extend(ignition_reasons)
        reasons.append("Aktivitas menyala sebelum momentum penuh")
    else:
        score = max(leakage, momentum, ignition_score)

    output.update(
        {
            "phase": phase,
            "score": round(score, 1),
            "leakage_score": round(leakage, 1),
            "momentum_score": round(momentum, 1),
            "ignition_score": round(ignition_score, 1),
            "micro_confirmed": micro_confirmed,
            "reasons": reasons,
        }
    )
    return output


def relaxed_anti_falling_knife_pass(result: dict[str, Any]) -> bool:
    """Hard safety gate for Filter 2.

    Filter 2 may relax momentum/activity thresholds, but it must not relax the
    existing falling-knife protection. Price near the intraday low is also
    rejected when range position is available.
    """
    if not bool(result.get("anti_falling_knife_pass")):
        return False
    if not bool(result.get("not_overextended")):
        return False

    position = result.get("position_in_range")
    if position is not None and float(position) < FILTER2_MIN_POSITION_IN_RANGE:
        return False

    return True


def apply_relaxed_micro_confirmation(
    result: dict[str, Any],
    micro: dict[str, Any],
) -> dict[str, Any]:
    """Filter 2: same momentum family as Filter 1 with looser thresholds.

    The scoring formulas are unchanged. Only confirmation thresholds are
    relaxed. Anti-falling-knife remains a mandatory hard gate.
    """
    output = {**result, **micro}

    micro_score = float(micro.get("micro_score") or 0.0)
    adjustment = (micro_score - 50.0) * 0.25
    leakage = max(0.0, min(100.0, float(result["leakage_score"]) + adjustment))
    momentum = max(0.0, min(100.0, float(result["momentum_score"]) + adjustment))

    reasons = list(result.get("reasons") or [])
    reasons.extend(micro.get("micro_reasons") or [])
    safe = relaxed_anti_falling_knife_pass(result)

    ignition_score, ignition_reasons = score_ignition(result, micro)
    volume_spike = result.get("volume_spike")
    interval_return = result.get("interval_return_pct")
    buy_ratio = micro.get("aggressive_buy_ratio")

    phase = "NONE"
    score = max(leakage, momentum, ignition_score)

    if (
        safe
        and micro_score >= FILTER2_MICRO_MIN_CONFIRM_SCORE
        and momentum >= FILTER2_MOMENTUM_THRESHOLD
        and _gte(result.get("breakout_pct"), FILTER2_PRE_MOMENTUM_MIN_BREAKOUT_PCT)
        and _gte(result.get("interval_return_pct"), FILTER2_PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT)
    ):
        phase = "POWERFUL_MOMENTUM"
        score = momentum
        reasons.append("Filter 2: momentum confirmation dengan threshold relaxed")
    elif (
        safe
        and micro_score >= FILTER2_PRE_MOMENTUM_MIN_MICRO_SCORE
        and momentum >= FILTER2_PRE_MOMENTUM_THRESHOLD
        and (result.get("activity_spike") or 0) >= 1.20
        and _gte(
            result.get("interval_return_pct"),
            FILTER2_PRE_MOMENTUM_MIN_INTERVAL_RETURN_PCT,
        )
        and _gte(
            result.get("breakout_pct"),
            FILTER2_PRE_MOMENTUM_MIN_BREAKOUT_PCT,
        )
        and (result.get("position_in_range") or 0)
            >= FILTER2_PRE_MOMENTUM_MIN_POSITION_IN_RANGE
        and (micro.get("aggressive_buy_ratio") or 0)
            >= FILTER2_PRE_MOMENTUM_MIN_AGGRESSIVE_BUY
    ):
        phase = "PRE_MOMENTUM"
        score = momentum
        reasons.append("Filter 2: pre-momentum relaxed, buyer tetap terkonfirmasi")
    elif (
        safe
        and micro_score >= FILTER2_MICRO_MIN_CONFIRM_SCORE
        and leakage >= FILTER2_LEAKAGE_THRESHOLD
    ):
        phase = "EARLY_LEAKAGE"
        score = leakage
        reasons.append("Filter 2: leakage relaxed dengan anti-falling-knife aktif")
    elif (
        safe
        and ignition_score >= FILTER2_IGNITION_THRESHOLD
        and volume_spike is not None
        and float(volume_spike) >= FILTER2_IGNITION_MIN_VOLUME_SPIKE
        and micro_score >= FILTER2_IGNITION_MIN_MICRO_SCORE
        and buy_ratio is not None
        and float(buy_ratio) >= FILTER2_IGNITION_MIN_AGGRESSIVE_BUY
        and interval_return is not None
        and float(interval_return) >= IGNITION_MIN_INTERVAL_RETURN_PCT
        and float(interval_return) <= IGNITION_MAX_INTERVAL_RETURN_PCT
    ):
        phase = "EARLY_IGNITION"
        score = ignition_score
        reasons.extend(ignition_reasons)
        reasons.append("Filter 2: ignition relaxed, anti-falling-knife tetap lolos")

    output.update(
        {
            "phase": phase,
            "score": round(score, 1),
            "leakage_score": round(leakage, 1),
            "momentum_score": round(momentum, 1),
            "ignition_score": round(ignition_score, 1),
            "micro_confirmed": micro_score >= FILTER2_MICRO_MIN_CONFIRM_SCORE,
            "filter2_relaxed": True,
            "anti_falling_knife_relaxed_pass": safe,
            "reasons": reasons,
        }
    )
    return output
