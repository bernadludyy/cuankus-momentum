"""Private daily MACD crossover watch from cached IDX summary and EOD bars."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from scanner.market_hours import WIB, is_idx_market_window
from scanner.top_gainer import _CODE, _day, _items, _num

NAMESPACE = "bot4-macd-gc-private-v1"


def _ema(values: list[float], period: int) -> list[float]:
    """SMA seed, then standard EMA; output starts at the first full period."""
    result = [sum(values[:period]) / period]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def compute_indicators(closes: list[float], volumes: list[float]) -> dict:
    if len(closes) < 61 or len(closes) != len(volumes):
        raise ValueError("MACD watch requires 60 completed sessions plus live bar")
    fast, slow = _ema(closes, 12), _ema(closes, 26)
    macd = [a - b for a, b in zip(fast[14:], slow)]
    signal = _ema(macd, 9)
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(0, x) for x in deltas[:14]) / 14
    loss = sum(max(0, -x) for x in deltas[:14]) / 14
    rsi = []
    for i in range(14, len(closes)):
        if i > 14:
            gain = (gain * 13 + max(0, deltas[i - 1])) / 14
            loss = (loss * 13 + max(0, -deltas[i - 1])) / 14
        rsi.append(100 * gain / (gain + loss) if gain + loss else 50.0)
    return {"previous_macd": macd[-2], "previous_signal": signal[-2],
            "macd": macd[-1], "signal": signal[-1],
            "rsi": rsi[-1], "previous_rsi": rsi[-2],
            "ema20": _ema(closes, 20)[-1],
            "volume_ema20_previous": _ema(volumes[:-1], 20)[-1]}


def _live_row(row: dict, when: datetime, max_price: float) -> dict | None:
    code = str(row.get("StockCode") or row.get("Code") or row.get("code") or "").strip().upper()
    if not _CODE.fullmatch(code) or _day(row) != when.astimezone(WIB).date():
        return None
    price = _num(row, "Close", "close")
    previous = _num(row, "Previous", "previous", "PrevClose", "prev_close")
    op = _num(row, "OpenPrice", "Open", "open")
    high, low = _num(row, "High", "high"), _num(row, "Low", "low")
    volume, value = _num(row, "Volume", "volume"), _num(row, "Value", "value")
    if any(x is None or x <= 0 for x in (price, previous, op, high, low, volume, value)):
        return None
    if not (price < max_price and low <= op <= high and low <= price <= high and high > low):
        return None
    change = (price / previous - 1) * 100
    position = (price - low) / (high - low)
    if not (0 < change < 6 and value > 3e9 and price >= op and position >= 0.65):
        return None
    return {"ticker": code, "price": price, "previous": previous,
            "gain_pct": change, "volume": volume, "value": value,
            "position_pct": position * 100, "low": low}


def evaluate_macd_gc(row: dict, history: Any, *, when: datetime,
                     last_session: date, max_price: float = 500) -> dict | None:
    live = _live_row(row, when, max_price)
    today = when.astimezone(WIB).date()
    if live is None or not 0 < (today - last_session).days <= 7:
        return None
    dates = {}
    for bar in _items(history):
        day = _day(bar)
        if day is None:
            return None
        if day >= today:
            continue
        close, volume = _num(bar, "close", "Close"), _num(bar, "volume", "Volume")
        high, low = _num(bar, "high", "High"), _num(bar, "low", "Low")
        if (None in (close, volume, high, low) or close <= 0 or volume < 0
                or low <= 0 or not low <= close <= high):
            return None
        canonical = (close, volume, high, low)
        if day in dates and dates[day] != canonical:
            return None
        dates[day] = canonical
    ordered = sorted(dates)
    if len(ordered) < 60 or ordered[-1] != last_session:
        return None
    bars = [dates[d] for d in ordered[-120:]]
    # A corporate-action/reference mismatch invalidates the unadjusted series.
    if abs(bars[-1][0] - live["previous"]) > 1e-6:
        return None
    metrics = compute_indicators([x[0] for x in bars] + [live["price"]],
                                 [x[1] for x in bars] + [live["volume"]])
    baseline = metrics["volume_ema20_previous"]
    if baseline <= 0:
        return None
    ratio = live["volume"] / baseline
    extension = (live["price"] / metrics["ema20"] - 1) * 100
    if not (metrics["previous_macd"] <= metrics["previous_signal"]
            and metrics["macd"] > metrics["signal"]
            and 40 <= metrics["rsi"] < 60
            and metrics["rsi"] > metrics["previous_rsi"]
            and ratio > 1.2 and 0 < extension <= 5):
        return None
    return {**live, **metrics, "volume_ratio": ratio,
            "ema_extension_pct": extension, "history_sessions": len(bars),
            "history_last_date": last_session.isoformat(),
            "macd_zone": "DI ATAS NOL" if metrics["macd"] >= 0 else "DI BAWAH NOL"}


def format_macd_alert(item: dict, *, when: datetime) -> str:
    local = when.astimezone(WIB)
    return "\n".join([
        f"{item['ticker']} | MACD GC DAILY | PRIVATE",
        f"Rp{item['price']:,.0f} ({item['gain_pct']:+.2f}%) | SEMENTARA / INTRADAY",
        f"Stock-summary tanggal {local:%Y-%m-%d} | diperiksa {local:%H:%M} WIB",
        f"EOD terakhir {item['history_last_date']} | {item['history_sessions']} sesi",
        f"MACD(12,26,9) {item['macd']:.4f} > signal {item['signal']:.4f} | {item['macd_zone']}",
        f"RSI14 {item['rsi']:.1f} naik dari {item['previous_rsi']:.1f}",
        f"Value Rp{item['value'] / 1e9:.2f} miliar | Volume {item['volume_ratio']:.2f}x EMA20 EOD",
        f"Harga > EMA20 Rp{item['ema20']:.2f} (+{item['ema_extension_pct']:.2f}%) | Posisi range {item['position_pct']:.0f}%",
        "Interpretasi: crossover bullish harian sementara; dapat batal sebelum penutupan.",
        "Tindakan: pantau konfirmasi penutupan dan struktur support sebelum entry.",
        "Belum dikonfirmasi: broker, orderbook, dan aliran pembeli agresif.",
        "Sumber: snapshot cache IDX + arsip EOD; bukan tick real-time. Maks. 1 alert/saham/hari.",
    ])


def run_macd_gc(payload: Any, *, when: datetime, history_loader: Callable,
                sender: Callable, store_factory: Callable,
                max_price: float = 500, max_alerts: int = 3) -> dict:
    if when.tzinfo is None:
        raise ValueError("when must be timezone aware")
    stats = {"status": "outside_market_hours", "prefilter": 0,
             "history_available": 0, "matched": 0, "sent": 0, "failed": 0}
    if not is_idx_market_window(when):
        return stats
    rows = {}
    for row in _items(payload):
        live = _live_row(row, when, max_price)
        if live:
            rows.setdefault(live["ticker"], row)
    stats.update(status="no_candidates", prefilter=len(rows))
    if not rows:
        return stats
    snapshot = history_loader(sorted(rows), when.astimezone(WIB).date())
    histories = snapshot.get("histories") or {}
    last_session = snapshot.get("last_session")
    stats["history_available"] = sum(bool(histories.get(code)) for code in rows)
    if not isinstance(last_session, date):
        stats["status"] = "history_unavailable"
        return stats
    candidates = []
    for code, row in rows.items():
        candidate = evaluate_macd_gc(row, histories.get(code, []), when=when,
                                     last_session=last_session, max_price=max_price)
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda x: (-x["volume_ratio"], -x["value"], x["ticker"]))
    stats.update(matched=len(candidates), status="evaluated")
    attempts = 0
    for item in candidates:
        if attempts >= max(1, min(max_alerts, 3)):
            break
        store = store_factory(item["ticker"])
        day = when.astimezone(WIB).date()
        if not store.claim(day):
            continue
        attempts += 1
        try:
            response = sender(format_macd_alert(item, when=when), target="private")
            delivered = bool(response.get("ok"))
        except Exception:
            store.complete(day, success=False)
            raise
        store.complete(day, success=delivered)
        stats["sent" if delivered else "failed"] += 1
    return stats
