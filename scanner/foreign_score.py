from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_foreign_metrics(
    summary_row: dict[str, Any] | None,
    price: float | None = None,
) -> dict[str, Any]:
    """Build a 0-100 intraday foreign-flow confirmation score.

    This is informational only: it does not change signal phase, candidate
    selection, or Telegram alert eligibility.
    """
    row = summary_row or {}

    foreign_buy = _num(
        row.get("ForeignBuy", row.get("foreignBuy", row.get("foreign_buy")))
    )
    foreign_sell = _num(
        row.get("ForeignSell", row.get("foreignSell", row.get("foreign_sell")))
    )
    volume = _num(row.get("Volume", row.get("volume")))
    total_value = _num(row.get("Value", row.get("value")))
    reference_price = _num(price)
    if reference_price is None:
        reference_price = _num(
            row.get("Close", row.get("close", row.get("Price", row.get("price"))))
        )

    if foreign_buy is None or foreign_sell is None:
        return {
            "foreign_score": None,
            "foreign_status": "UNAVAILABLE",
            "foreign_buy_shares": foreign_buy,
            "foreign_sell_shares": foreign_sell,
            "net_foreign_shares": None,
            "foreign_buy_value": None,
            "foreign_sell_value": None,
            "net_foreign_value": None,
            "net_foreign_ratio_pct": None,
            "foreign_buy_dominance_pct": None,
        }

    net_foreign = foreign_buy - foreign_sell
    foreign_total = foreign_buy + foreign_sell

    foreign_buy_value = None
    foreign_sell_value = None
    net_foreign_value = None
    if reference_price is not None and reference_price > 0:
        foreign_buy_value = foreign_buy * reference_price
        foreign_sell_value = foreign_sell * reference_price
        net_foreign_value = net_foreign * reference_price

    net_ratio_pct = None
    if (
        net_foreign_value is not None
        and total_value is not None
        and total_value > 0
    ):
        net_ratio_pct = net_foreign_value / total_value * 100.0
    elif volume is not None and volume > 0:
        net_ratio_pct = net_foreign / volume * 100.0

    buy_dominance_pct = None
    foreign_total_value = None
    if foreign_buy_value is not None and foreign_sell_value is not None:
        foreign_total_value = foreign_buy_value + foreign_sell_value

    if foreign_total_value is not None and foreign_total_value > 0:
        buy_dominance_pct = foreign_buy_value / foreign_total_value * 100.0
    elif foreign_total > 0:
        buy_dominance_pct = foreign_buy / foreign_total * 100.0

    if net_ratio_pct is None or buy_dominance_pct is None:
        score = None
    else:
        # Neutral foreign flow is approximately 50/100:
        # - net foreign ratio: -10%..+10% => 0..60 points
        # - foreign buy dominance: 30%..70% => 0..40 points
        net_component = (
            (_clamp(net_ratio_pct, -10.0, 10.0) + 10.0)
            / 20.0
            * 60.0
        )
        dominance_component = (
            (_clamp(buy_dominance_pct, 30.0, 70.0) - 30.0)
            / 40.0
            * 40.0
        )
        score = round(_clamp(net_component + dominance_component, 0.0, 100.0), 1)

    if score is None:
        status = "UNAVAILABLE"
    elif score >= 75:
        status = "STRONG NET FOREIGN BUY"
    elif score >= 60:
        status = "POSITIVE FOREIGN FLOW"
    elif score >= 40:
        status = "NEUTRAL / MIXED"
    elif score >= 25:
        status = "FOREIGN SELLING"
    else:
        status = "STRONG FOREIGN SELLING"

    return {
        "foreign_score": score,
        "foreign_status": status,
        "foreign_buy_shares": foreign_buy,
        "foreign_sell_shares": foreign_sell,
        "net_foreign_shares": net_foreign,
        "foreign_buy_value": foreign_buy_value,
        "foreign_sell_value": foreign_sell_value,
        "net_foreign_value": net_foreign_value,
        "net_foreign_ratio_pct": net_ratio_pct,
        "foreign_buy_dominance_pct": buy_dominance_pct,
    }
