from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from typing import Any

from config import (
    BIG_PRINT_MIN_LOT,
    REVERSAL_BUILDING_SCORE,
    REVERSAL_CONFIRM_SCORE,
    REVERSAL_FAILED_ALERT_ENABLED,
    REVERSAL_MIN_ABSORBED_SELL_IDR,
    REVERSAL_MIN_AGGRESSIVE_BUY_RATIO,
    REVERSAL_MIN_BID_PERCENT,
    REVERSAL_MIN_NET_AGGRESSION_IDR,
    REVERSAL_MIN_NEW_TRADES,
)
from scanner.market_data import fetch_orderbook, fetch_running_trades
from scanner.market_hours import WIB
from scanner.microstructure import _num, _unwrap, analyze_orderbook
from storage.db import (
    get_snapshots_since,
    save_reversal_trade_if_new,
    update_reversal_watch,
)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _scale(value: float | None, low: float, high: float, points: float) -> float:
    if value is None:
        return 0.0
    if value <= low:
        return 0.0
    if value >= high:
        return points
    return (value - low) / (high - low) * points


def _trade_datetime(time_text: Any, reference: datetime) -> datetime | None:
    if time_text is None:
        return None
    text = str(time_text).strip()
    try:
        clock = datetime.strptime(text, "%H:%M:%S").time()
    except ValueError:
        return None
    return datetime.combine(reference.date(), clock, tzinfo=WIB)


def _trade_value(item: dict[str, Any], lots: float) -> tuple[float | None, float | None]:
    direct = None
    for key in ("tradeValue", "transactionValue", "value", "Value"):
        direct = _num(item.get(key))
        if direct is not None and direct >= 0:
            break

    price = None
    for key in ("price", "Price", "tradePrice", "last", "Last"):
        price = _num(item.get(key))
        if price is not None and price > 0:
            break

    if direct is not None:
        return direct, price
    if price is not None and lots > 0:
        return price * lots * 100.0, price
    return None, price


def _canonical_trade_signature(item: dict[str, Any]) -> str:
    # Prefer exchange/API sequence-like identifiers when present.
    for key in (
        "id", "tradeId", "tradeID", "tradeNo", "tradeNumber",
        "sequence", "seq", "matchId", "matchID",
    ):
        value = item.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"

    normalized = json.dumps(
        item,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_new_matched_trades(
    ticker: str,
    payload: Any,
    *,
    touch_ts: str,
) -> dict[str, Any]:
    data = _unwrap(payload)
    items = (
        [x for x in (data.get("items") or []) if isinstance(x, dict)]
        if isinstance(data, dict)
        else []
    )

    try:
        touch_dt = datetime.fromisoformat(touch_ts)
        touch_dt = (
            touch_dt.replace(tzinfo=WIB)
            if touch_dt.tzinfo is None
            else touch_dt.astimezone(WIB)
        )
    except Exception:
        touch_dt = datetime.now(WIB)

    # Duplicate identical fallback records are distinguished inside one payload.
    occurrences: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []

    for item in items:
        trade_dt = _trade_datetime(item.get("time"), touch_dt)
        if trade_dt is None or trade_dt < touch_dt:
            continue

        action = str(item.get("action") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue

        lots = _num(item.get("lots"))
        if lots is None:
            lots = _num(item.get("lot"))
        lots = lots or 0.0
        value, price = _trade_value(item, lots)
        if value is None or value < 0:
            continue

        base_key = _canonical_trade_signature(item)
        occurrences[base_key] += 1
        ordinal = occurrences[base_key]
        trade_key = f"{base_key}:{ordinal}"

        if not save_reversal_trade_if_new(
            ticker=ticker,
            trade_day=trade_dt.date().isoformat(),
            trade_key=trade_key,
            trade_ts=trade_dt.isoformat(timespec="seconds"),
            action=action,
            price=price,
            lots=lots,
            trade_value=value,
        ):
            continue

        rows.append(
            {
                "trade_ts": trade_dt.isoformat(timespec="seconds"),
                "action": action,
                "lots": lots,
                "price": price,
                "value": value,
            }
        )

    buy_rows = [x for x in rows if x["action"] == "BUY"]
    sell_rows = [x for x in rows if x["action"] == "SELL"]
    buy_value = sum(x["value"] for x in buy_rows)
    sell_value = sum(x["value"] for x in sell_rows)
    buy_lots = sum(x["lots"] for x in buy_rows)
    sell_lots = sum(x["lots"] for x in sell_rows)
    total_lots = buy_lots + sell_lots

    big_buy_lots = sum(x["lots"] for x in buy_rows if x["lots"] >= BIG_PRINT_MIN_LOT)
    big_sell_lots = sum(x["lots"] for x in sell_rows if x["lots"] >= BIG_PRINT_MIN_LOT)
    big_total = big_buy_lots + big_sell_lots

    return {
        "new_trade_count": len(rows),
        "new_buy_trade_count": len(buy_rows),
        "new_sell_trade_count": len(sell_rows),
        "new_buy_value": buy_value,
        "new_sell_value": sell_value,
        "new_net_aggression": buy_value - sell_value,
        "new_buy_lots": buy_lots,
        "new_sell_lots": sell_lots,
        "new_aggressive_buy_ratio": (
            buy_lots / total_lots if total_lots > 0 else None
        ),
        "new_big_buy_ratio": (
            big_buy_lots / big_total if big_total > 0 else None
        ),
    }


def detect_pullback_touch(
    watch: dict[str, Any],
    *,
    current_price: float,
) -> tuple[str | None, float | None]:
    lower = float(watch["pullback_lower"])
    upper = float(watch["pullback_upper"])
    invalidation = float(watch["invalidation_price"])

    snapshots = get_snapshots_since(watch["ticker"], watch["alert_ts"])
    first_touch_ts = None
    low_seen = None
    for row in snapshots:
        price = _num(row.get("last"))
        if price is None:
            continue
        low_seen = price if low_seen is None else min(low_seen, price)
        if first_touch_ts is None and invalidation <= price <= upper:
            first_touch_ts = str(row.get("ts"))

    if first_touch_ts is None and invalidation <= current_price <= upper:
        first_touch_ts = datetime.now(WIB).isoformat(timespec="seconds")
        low_seen = current_price if low_seen is None else min(low_seen, current_price)

    return first_touch_ts, low_seen


def score_live_reversal(
    watch: dict[str, Any],
    *,
    current_price: float,
    book: dict[str, Any],
    batch: dict[str, Any],
) -> dict[str, Any]:
    prev_buy = float(watch.get("cumulative_buy_value") or 0.0)
    prev_sell = float(watch.get("cumulative_sell_value") or 0.0)
    prev_buy_lots = float(watch.get("cumulative_buy_lots") or 0.0)
    prev_sell_lots = float(watch.get("cumulative_sell_lots") or 0.0)
    prev_count = int(watch.get("new_trade_count") or 0)

    cumulative_buy = prev_buy + float(batch["new_buy_value"])
    cumulative_sell = prev_sell + float(batch["new_sell_value"])
    cumulative_buy_lots = prev_buy_lots + float(batch["new_buy_lots"])
    cumulative_sell_lots = prev_sell_lots + float(batch["new_sell_lots"])
    cumulative_count = prev_count + int(batch["new_trade_count"])
    cumulative_delta = cumulative_buy - cumulative_sell

    total_lots = cumulative_buy_lots + cumulative_sell_lots
    cumulative_buy_ratio = (
        cumulative_buy_lots / total_lots if total_lots > 0 else None
    )

    previous_delta = watch.get("last_delta_value")
    batch_delta = float(batch["new_net_aggression"])
    delta_flip = (
        previous_delta is not None
        and float(previous_delta) <= 0
        and batch_delta > 0
    )

    bid_percent = _num(book.get("bid_percent"))
    low_seen = _num(watch.get("low_seen"))
    if low_seen is None:
        low_seen = current_price
    low_seen = min(low_seen, current_price)

    sell_absorption = (
        cumulative_sell >= REVERSAL_MIN_ABSORBED_SELL_IDR
        and current_price >= low_seen
        and bid_percent is not None
        and bid_percent >= REVERSAL_MIN_BID_PERCENT
    )

    # 30 — delta/CVD.
    delta_points = _scale(
        cumulative_delta,
        0.0,
        max(REVERSAL_MIN_NET_AGGRESSION_IDR * 4.0, 1.0),
        25.0,
    )
    if delta_flip:
        delta_points += 5.0

    # 25 — absorption: matched selling exists but price is no longer breaking.
    absorption_points = 25.0 if sell_absorption else (
        12.0 if cumulative_sell >= REVERSAL_MIN_ABSORBED_SELL_IDR else 0.0
    )

    # 20 — buyer takeover from matched lots since pullback touch.
    buy_ratio_points = _scale(cumulative_buy_ratio, 0.50, 0.75, 20.0)

    # 10 — current orderbook confirmation.
    bid_points = _scale(bid_percent, 45.0, 65.0, 10.0)

    # 10 — trade participation / big-buy participation.
    velocity_points = _scale(
        float(cumulative_count),
        float(max(1, REVERSAL_MIN_NEW_TRADES)),
        float(max(REVERSAL_MIN_NEW_TRADES + 7, 10)),
        6.0,
    )
    velocity_points += _scale(batch.get("new_big_buy_ratio"), 0.50, 0.80, 4.0)

    # 5 — price reclaim. This remains a hard condition for actual confirmation.
    reclaimed = current_price >= float(watch["reversal_trigger"])
    reclaim_points = 5.0 if reclaimed else 0.0

    score = _clamp(
        delta_points
        + absorption_points
        + buy_ratio_points
        + bid_points
        + velocity_points
        + reclaim_points
    )

    hard_confirm = (
        score >= REVERSAL_CONFIRM_SCORE
        and reclaimed
        and cumulative_count >= REVERSAL_MIN_NEW_TRADES
        and cumulative_delta >= REVERSAL_MIN_NET_AGGRESSION_IDR
        and cumulative_buy_ratio is not None
        and cumulative_buy_ratio >= REVERSAL_MIN_AGGRESSIVE_BUY_RATIO
        and batch_delta > 0
        and bid_percent is not None
        and bid_percent >= REVERSAL_MIN_BID_PERCENT
    )

    if hard_confirm:
        status = "ENTRY_CONFIRMED"
    elif score >= REVERSAL_BUILDING_SCORE:
        status = "REVERSAL_BUILDING"
    else:
        status = "WAIT"

    return {
        "status": status,
        "score": round(score, 1),
        "current_price": current_price,
        "low_seen": low_seen,
        "bid_percent": bid_percent,
        "cumulative_buy_value": cumulative_buy,
        "cumulative_sell_value": cumulative_sell,
        "cumulative_delta": cumulative_delta,
        "cumulative_buy_lots": cumulative_buy_lots,
        "cumulative_sell_lots": cumulative_sell_lots,
        "cumulative_buy_ratio": cumulative_buy_ratio,
        "cumulative_trade_count": cumulative_count,
        "batch_delta": batch_delta,
        "batch_trade_count": int(batch["new_trade_count"]),
        "batch_buy_ratio": batch.get("new_aggressive_buy_ratio"),
        "big_buy_ratio": batch.get("new_big_buy_ratio"),
        "delta_flip": delta_flip,
        "selling_absorption_live": sell_absorption,
        "reclaimed": reclaimed,
    }


def evaluate_live_reversal_watch(
    watch: dict[str, Any],
    *,
    current_price: float,
    running_trade_min_lot: int = 10,
) -> dict[str, Any]:
    ticker = str(watch["ticker"]).upper()
    now = datetime.now(WIB).isoformat(timespec="seconds")
    invalidation = float(watch["invalidation_price"])

    if current_price < invalidation:
        update_reversal_watch(
            ticker,
            state="CANCELLED",
            last_price=current_price,
            cancelled_ts=now,
            last_eval_ts=now,
        )
        return {
            "status": "CANCELLED",
            "ticker": ticker,
            "current_price": current_price,
            "reason": "Harga breakdown di bawah invalidation",
            "send_failed_alert": REVERSAL_FAILED_ALERT_ENABLED,
        }

    state = str(watch.get("state") or "WAIT_PULLBACK")
    touch_ts = watch.get("touch_ts")
    low_seen = _num(watch.get("low_seen"))

    if state == "WAIT_PULLBACK":
        detected_touch_ts, detected_low = detect_pullback_touch(
            watch,
            current_price=current_price,
        )
        if detected_touch_ts is None:
            update_reversal_watch(
                ticker,
                last_price=current_price,
                last_eval_ts=now,
            )
            return {
                "status": "WAIT_PULLBACK",
                "ticker": ticker,
                "current_price": current_price,
            }
        touch_ts = detected_touch_ts
        low_seen = detected_low if detected_low is not None else current_price
        update_reversal_watch(
            ticker,
            state="PULLBACK_ACTIVE",
            touch_ts=touch_ts,
            low_seen=low_seen,
            last_price=current_price,
            last_eval_ts=now,
        )
        watch = {**watch, "state": "PULLBACK_ACTIVE", "touch_ts": touch_ts, "low_seen": low_seen}

    if not touch_ts:
        return {"status": "WAIT_PULLBACK", "ticker": ticker, "current_price": current_price}

    orderbook_payload = fetch_orderbook(ticker)
    running_payload = fetch_running_trades(
        ticker,
        min_lot=max(1, int(running_trade_min_lot)),
    )
    book = analyze_orderbook(orderbook_payload)
    batch = extract_new_matched_trades(
        ticker,
        running_payload,
        touch_ts=str(touch_ts),
    )

    scored = score_live_reversal(
        watch,
        current_price=current_price,
        book=book,
        batch=batch,
    )

    # Keep confirmed candidates active until Telegram delivery succeeds.
    # The caller marks CONFIRMED only after a successful notification, so a
    # transient Telegram failure can retry on the next scanner cycle.
    update_reversal_watch(
        ticker,
        state="PULLBACK_ACTIVE",
        low_seen=scored["low_seen"],
        last_price=current_price,
        cumulative_buy_value=scored["cumulative_buy_value"],
        cumulative_sell_value=scored["cumulative_sell_value"],
        cumulative_buy_lots=scored["cumulative_buy_lots"],
        cumulative_sell_lots=scored["cumulative_sell_lots"],
        new_trade_count=scored["cumulative_trade_count"],
        last_delta_value=scored["batch_delta"],
        last_score=scored["score"],
        last_eval_ts=now,
    )

    return {
        **scored,
        "ticker": ticker,
        "touch_ts": touch_ts,
        "source_lane": watch.get("source_lane"),
        "source_phase": watch.get("source_phase"),
        "target": watch.get("target") or "all",
        "alert_price": float(watch["alert_price"]),
        "pullback_lower": float(watch["pullback_lower"]),
        "pullback_upper": float(watch["pullback_upper"]),
        "reversal_trigger": float(watch["reversal_trigger"]),
        "invalidation_price": invalidation,
    }
