"""Private Top Gainer Potential watch from Bot 4's existing intraday quotes.

This is a *watchlist heuristic*, not a Stockbit metric or tested win-rate.
No new ZAPI calls, historical downloads, orderbook calls, or workflow jobs.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any, Callable

from scanner.top_gainer import WIB, NeonDailyDelivery, _day, _items, _num, _CODE

POTENTIAL_NAMESPACE = "bot4-topgainer-potential-daily-v1"


def idx_tick(price: float) -> int:
    """Regular IDX share-price tick ladder; use reference at proposed price."""
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def _next_tick(price: float) -> int:
    step = idx_tick(price)
    return int(math.floor(price / step) * step + step)


def _prior_tick(price: float) -> int:
    step = idx_tick(price)
    return int(math.ceil(price / step) * step - step)


def _previous_close_map(payload: Any, trade_date: date) -> dict[str, float]:
    """Historical close only; never use stale activity/turnover as live."""
    rows = [r for r in _items(payload) if _day(r) is not None and _day(r) < trade_date]
    last_date = max((_day(r) for r in rows), default=None)
    if last_date is None or (trade_date - last_date).days > 7:
        return {}
    data: dict[str, float] = {}
    for row in rows:
        if _day(row) != last_date:
            continue
        code = str(row.get("StockCode") or row.get("Code") or row.get("code") or "").upper().strip()
        close = _num(row, "Close", "close")
        if _CODE.fullmatch(code) and close is not None and close > 0:
            data[code] = close
    return data


def _session_prices(snapshots: list[dict], *, day: date, latest: datetime) -> list[float]:
    prices = []
    for row in snapshots:
        try:
            dt = datetime.fromisoformat(str(row["ts"]))
        except (ValueError, KeyError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WIB)
        dt = dt.astimezone(WIB)
        if dt.date() != day or latest - dt > timedelta(minutes=45) or dt > latest:
            continue
        price = _num(row, "last")
        if price is not None and price > 0:
            prices.append(price)
    return prices


def evaluate_potential(
    quote: dict,
    features: dict,
    snapshots: list[dict],
    *,
    previous_close: float | None,
    when: datetime,
) -> dict | None:
    """Score 0-100 with mandatory accelerated frequency/value and early price move.

    Price 20, frequency 25, value 25, volume 10, structure 20.
    No score is awarded for missing micro data or unavailable VWAP.
    """
    local = when.astimezone(WIB)
    ticker = str(quote.get("ticker") or "").upper().strip()
    last = _num(quote, "last")
    prev = _num(quote, "prev_close")
    if prev is None or prev <= 0:
        prev = previous_close
    op = _num(quote, "open")
    high = _num(quote, "high")
    low = _num(quote, "low")
    if not _CODE.fullmatch(ticker) or not last or not prev or prev <= 0:
        return None
    gain = (last / prev - 1.0) * 100.0
    # Avoid late/chasing entries and downward-price false positives.
    if not 0.8 <= gain <= 8.0:
        return None
    if not (low is not None and high is not None and high >= low > 0 and low <= last <= high):
        return None
    if not (op is not None and op > 0 and last >= op):
        return None

    freq = _num(features, "frequency_spike")
    value = _num(features, "value_spike")
    volume = _num(features, "volume_spike")
    interval = _num(features, "interval_return_pct")
    breakout = _num(features, "breakout_pct")
    position = _num(features, "position_in_range")
    f_rate = _num(features, "frequency_rate")
    v_rate = _num(features, "value_rate")
    if freq is None or value is None or interval is None or position is None:
        return None
    if freq < 1.20 or value < 1.20 or interval < 0 or position < 0.65:
        return None
    if f_rate is None or v_rate is None or f_rate <= 0 or v_rate <= 0:
        return None

    prices = _session_prices(snapshots, day=local.date(), latest=local)
    if len(prices) < 5:
        return None
    previous_prices = prices[:-1][-5:]
    if len(previous_prices) < 4:
        return None

    momentum_points = 8 + (6 if interval >= 0.10 else 0) + (6 if last > op else 0)
    frequency_points = 25 if freq >= 1.80 else 18 if freq >= 1.40 else 12
    value_points = 25 if value >= 1.80 else 18 if value >= 1.40 else 12
    volume_points = 10 if volume is not None and volume >= 1.50 else 6 if volume is not None and volume >= 1.20 else 0
    near_high = high > 0 and last / high >= 0.985
    structure_points = (
        (10 if breakout is not None and breakout >= 0 else 5 if breakout is not None and breakout >= -0.50 else 0)
        + (5 if position >= 0.80 else 0)
        + (5 if near_high else 0)
    )
    score = min(100, momentum_points + frequency_points + value_points + volume_points + structure_points)
    if score < 70:
        return None

    # Purely prospective confirmation: breakout the observed session high with
    # renewed frequency/value activity. The trigger is NOT a filled order.
    resistance = max(high, max(previous_prices))
    entry = _next_tick(resistance)
    support = min(previous_prices)
    invalidation = _prior_tick(support)
    risk = (entry - invalidation) / entry * 100 if entry > invalidation > 0 else None
    # Do not issue an entry level if the invalidation is too distant.
    valid_entry = (
        risk is not None and 0 < risk <= 5.0
        and (entry - last) / last * 100 <= 3.0
    )
    # Derived price reference only; not a forecast or TP recommendation.
    reference_2r = _next_tick(entry + 2 * (entry - invalidation)) if valid_entry else None
    return {
        "ticker": ticker,
        "price": last,
        "previous": prev,
        "gain_pct": round(gain, 2),
        "score": int(score),
        "frequency_spike": round(freq, 2),
        "value_spike": round(value, 2),
        "volume_spike": round(volume, 2) if volume is not None else None,
        "interval_return_pct": round(interval, 2),
        "breakout_pct": round(breakout, 2) if breakout is not None else None,
        "range_position_pct": round(position * 100, 0),
        "entry_trigger": entry if valid_entry else None,
        "invalidation": invalidation if valid_entry else None,
        "risk_pct": round(risk, 2) if risk is not None else None,
        "reference_2r": reference_2r,
        "status": "BREAKOUT WATCH" if valid_entry else "WATCH ONLY / RISK TOO WIDE",
    }


def rank_potential(
    quotes: list[dict],
    features: list[dict],
    *,
    summary_payload: Any,
    when: datetime,
    snapshot_loader: Callable[[str], list[dict]],
    count: int = 3,
) -> list[dict]:
    """Score only existing Bot 4 analyzed universe, with no additional API."""
    previous = _previous_close_map(summary_payload, when.astimezone(WIB).date())
    by_ticker = {str(x.get("ticker") or "").upper(): x for x in features}
    results = []
    for quote in quotes:
        ticker = str(quote.get("ticker") or "").upper().strip()
        item = by_ticker.get(ticker)
        if not item:
            continue
        last = _num(quote, "last")
        prev = _num(quote, "prev_close") or previous.get(ticker)
        if not last or not prev or not 0.8 <= (last / prev - 1) * 100 <= 8:
            continue
        result = evaluate_potential(
            quote, item, snapshot_loader(ticker),
            previous_close=previous.get(ticker), when=when,
        )
        if result is not None:
            results.append(result)
    results.sort(key=lambda r: (-r["score"], -r["frequency_spike"], r["ticker"]))
    return results[:max(1, min(int(count), 5))]


def _rupiah(price: int | float | None) -> str:
    if price is None:
        return "N/A"
    return f"Rp{price:,.0f}".replace(",", ".")


def format_potential_report(candidates: list[dict], *, when: datetime) -> str:
    local = when.astimezone(WIB)
    lines = [
        f"🔍 TOP GAINER POTENTIAL | {local:%d-%m-%Y %H:%M} WIB",
        "📩 PRIVATE • Universe Bot 4 • WATCH, bukan sinyal buy otomatis",
        f"Top {len(candidates)} kandidat | Gain awal 0,8%–8%",
        "",
    ]
    for i, x in enumerate(candidates, 1):
        lines.extend([
            f"{i}. {x['ticker']} | {_rupiah(x['price'])} ({x['gain_pct']:+.2f}%) | Skor {x['score']}/100",
            f"   Freq {x['frequency_spike']:.2f}x • Value {x['value_spike']:.2f}x • Volume "
            + (f"{x['volume_spike']:.2f}x" if x["volume_spike"] is not None else "N/A"),
            f"   Return interval {x['interval_return_pct']:+.2f}% • Range {x['range_position_pct']:.0f}%"
            + (f" • Breakout {x['breakout_pct']:+.2f}%" if x["breakout_pct"] is not None else ""),
            f"   Setup: {x['status']}",
        ])
        if x["entry_trigger"] is not None:
            lines.extend([
                f"   Trigger ≥ {_rupiah(x['entry_trigger'])} + konfirmasi buyer/freq/value",
                f"   Invalidasi < {_rupiah(x['invalidation'])} | Risiko struktur {x['risk_pct']:.2f}%",
                f"   Referensi 2R: {_rupiah(x['reference_2r'])} (simulasi, bukan prediksi)",
            ])
        else:
            lines.append("   Entry: TUNGGU. Jarak trigger/stop terlalu besar.")
        lines.append("")
    lines.extend([
        "Skor: momentum 20 + freq 25 + value 25 + volume 10 + struktur 20.",
        "VWAP, orderbook & smart money: belum dikonfirmasi bila tidak ada data.",
        "Harga trigger bisa berubah tiap snapshot; hindari mengejar ARA.",
        "🔕 Maks. 1 laporan potensial/hari, hanya jika skor ≥70.",
    ])
    return "\n".join(lines)


def send_daily_potential(
    quotes: list[dict],
    features: list[dict],
    *,
    summary_payload: Any,
    when: datetime,
    sender: Callable[..., dict],
    store: Any,
    snapshot_loader: Callable[[str], list[dict]],
    count: int = 3,
    after_hour: int = 10,
) -> str:
    """Independent private alert, retryable until a real candidate is found."""
    if when.tzinfo is None:
        raise ValueError("when must be timezone aware")
    local = when.astimezone(WIB)
    if local.weekday() > 4 or local.hour < after_hour or local.hour >= 16:
        return "outside_delivery_window"
    ranked = rank_potential(
        quotes, features, summary_payload=summary_payload, when=local,
        snapshot_loader=snapshot_loader, count=count,
    )
    if not ranked:
        return "no_confirmed_candidates"
    if not store.claim(local.date()):
        return "already_sent_or_claimed"
    try:
        response = sender(format_potential_report(ranked, when=local), target="private")
        delivered = bool(response.get("ok"))
    except Exception:
        store.complete(local.date(), success=False)
        raise
    store.complete(local.date(), success=delivered)
    return f"sent:{len(ranked)}:private" if delivered else "telegram_failed_retryable"
