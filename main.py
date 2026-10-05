from __future__ import annotations

import time

from bot.notifier import send_telegram
from project_fabric.observability import ObservabilityClient
from config import (
    BASE_DIR,
    BATCH_SIZE,
    FORCE_RUN,
    IGNITION_RESERVED_SLOTS,
    IGNITION_MIN_VOLUME_SPIKE,
    IGNITION_MIN_INTERVAL_RETURN_PCT,
    IGNITION_MAX_INTERVAL_RETURN_PCT,
    IGNITION_MAX_DAILY_CHANGE_PCT,
    MICRO_MAX_CANDIDATES,
    MICRO_MIN_PRE_SCORE,
    MARKET_CAP_FILTER_ENABLED,
    MIN_MARKET_CAP_IDR,
    MAX_MARKET_CAP_IDR,
    MARKET_CAP_CACHE_HOURS,
    MAX_ALERT_PRICE,
    DAILY_FILTER_ENABLED,
    DAILY_FILTER_CACHE_HOURS,
    STOCK_HISTORY_LENGTH,
    SHAREHOLDER_FILTER_ENABLED,
    MAX_SHAREHOLDERS,
    SHAREHOLDER_CACHE_HOURS,
    SHAREHOLDER_FAILURE_CACHE_HOURS,
    SHAREHOLDER_ANNOUNCEMENT_LENGTH,
    VOLUME_MA_SHORT,
    VOLUME_MA_LONG,
    VOLUME_MA_RATIO_MIN,
    VALUE_MA_WINDOW,
    MIN_VALUE_MA_IDR,
    TOP_MOVER_COUNT,
    USE_MULTI_QUOTE,
)
from scanner.daily_filters import (
    compute_daily_filter_metrics,
    daily_filter_is_eligible,
)
from scanner.foreign_score import build_foreign_metrics
from scanner.engine import (
    analyze_ticker,
    mark_alerted,
    mark_no_signal,
    mark_signal_present,
    should_alert,
)
from scanner.market_data import (
    discover_active_tickers,
    extract_market_cap,
    fetch_multi_quote,
    fetch_quote,
    get_stock_summary_row,
    fetch_stock_history,
)
from scanner.market_hours import is_idx_market_window
from scanner.shareholder_filter import fetch_latest_shareholder_count
from scanner.microstructure import apply_micro_confirmation, fetch_microstructure
from scanner.normalizer import extract_quotes, normalize_quote
from storage.db import (
    get_cached_daily_filter,
    get_cached_market_cap,
    get_cached_shareholder_filter,
    init_db,
    save_daily_filter,
    save_market_cap,
    save_shareholder_filter,
    save_snapshot,
)


_OBS = ObservabilityClient("cuankus-momentum", auto_migrate=False)


def load_universe() -> list[str]:
    path = BASE_DIR / "universe.txt"
    tickers: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip().upper()
        if item and not item.startswith("#"):
            tickers.append(item)

    return list(dict.fromkeys(tickers))


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def is_ignition_pre_candidate(result: dict) -> bool:
    volume_spike = result.get("volume_spike")
    interval_return = result.get("interval_return_pct")
    daily_change = result.get("daily_change_pct")

    if volume_spike is None or interval_return is None:
        return False

    return (
        float(volume_spike) >= IGNITION_MIN_VOLUME_SPIKE
        and IGNITION_MIN_INTERVAL_RETURN_PCT
            <= float(interval_return)
            <= IGNITION_MAX_INTERVAL_RETURN_PCT
        and (
            daily_change is None
            or float(daily_change) <= IGNITION_MAX_DAILY_CHANGE_PCT
        )
        and bool(result.get("anti_falling_knife_pass"))
        and bool(result.get("not_overextended"))
    )


def format_num(value, suffix=""):
    if value is None:
        return "-"
    return f"{value:.2f}{suffix}"


def format_shareholder_count(value) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}"


def format_shares(value) -> str:
    if value is None:
        return "-"
    value = float(value)
    sign = "+" if value > 0 else ""
    absolute = abs(value)
    if absolute >= 1e9:
        return f"{sign}{value / 1e9:.2f}B"
    if absolute >= 1e6:
        return f"{sign}{value / 1e6:.2f}M"
    if absolute >= 1e3:
        return f"{sign}{value / 1e3:.2f}K"
    return f"{sign}{value:.0f}"


def format_signed_pct(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def format_market_cap(value) -> str:
    if value is None:
        return "-"
    value = float(value)
    if value >= 1e12:
        return f"Rp{value / 1e12:.2f}T"
    if value >= 1e9:
        return f"Rp{value / 1e9:.2f}B"
    if value >= 1e6:
        return f"Rp{value / 1e6:.2f}M"
    return f"Rp{value:,.0f}"


def format_rupiah_compact(value) -> str:
    return format_market_cap(value)


def get_market_cap(ticker: str) -> float | None:
    cached = get_cached_market_cap(
        ticker,
        max_age_hours=MARKET_CAP_CACHE_HOURS,
    )
    if cached is not None:
        return cached

    raw = fetch_quote(ticker)
    market_cap = extract_market_cap(raw)
    if market_cap is not None:
        save_market_cap(ticker, market_cap)
    return market_cap


def market_cap_is_eligible(market_cap: float | None) -> bool:
    if not MARKET_CAP_FILTER_ENABLED:
        return True
    if market_cap is None:
        return False
    return MIN_MARKET_CAP_IDR < float(market_cap) < MAX_MARKET_CAP_IDR


def price_is_eligible(price: float | None) -> bool:
    if price is None:
        return False
    return float(price) < MAX_ALERT_PRICE


def shareholder_is_eligible(holder_count: int | None) -> bool:
    if not SHAREHOLDER_FILTER_ENABLED:
        return True
    if holder_count is None:
        return False
    return int(holder_count) < MAX_SHAREHOLDERS


def get_shareholder_metrics(ticker: str) -> dict:
    cached = get_cached_shareholder_filter(
        ticker,
        success_max_age_hours=SHAREHOLDER_CACHE_HOURS,
        failure_max_age_hours=SHAREHOLDER_FAILURE_CACHE_HOURS,
    )
    if cached is not None:
        return cached

    metrics = fetch_latest_shareholder_count(
        ticker,
        announcement_length=SHAREHOLDER_ANNOUNCEMENT_LENGTH,
    )
    save_shareholder_filter(
        ticker,
        holder_count=metrics.get("holder_count"),
        report_published_at=metrics.get("report_published_at"),
        announcement_id=metrics.get("announcement_id"),
        source_url=metrics.get("source_url"),
        status=str(metrics.get("status") or "UNKNOWN"),
    )
    return metrics


def prepare_shareholder_candidate(result: dict) -> bool:
    if not SHAREHOLDER_FILTER_ENABLED:
        return True

    if result.get("_shareholder_checked"):
        return bool(result.get("_shareholder_eligible"))

    ticker = result["ticker"]

    try:
        metrics = get_shareholder_metrics(ticker)
    except Exception as exc:
        metrics = {
            "holder_count": None,
            "report_published_at": None,
            "announcement_id": None,
            "source_url": None,
            "status": f"ERROR:{type(exc).__name__}",
        }
        print(f"[WARN] shareholder filter {ticker} gagal: {exc}")

    holder_count = metrics.get("holder_count")
    eligible = shareholder_is_eligible(holder_count)

    result["shareholder_count"] = holder_count
    result["shareholder_report_published_at"] = metrics.get("report_published_at")
    result["shareholder_status"] = metrics.get("status")
    result["_shareholder_checked"] = True
    result["_shareholder_eligible"] = eligible

    if not eligible:
        print(
            f"[SHAREHOLDER-SKIP] {ticker}: "
            f"holders={holder_count if holder_count is not None else '-'} "
            f"need<{MAX_SHAREHOLDERS}; "
            f"status={metrics.get('status')}"
        )

    return eligible


def prepare_market_cap_candidate(result: dict) -> bool:
    """Attach market cap and return whether the ticker is eligible.

    This runs before expensive orderbook/running-trade calls, so the six
    microstructure slots are reserved only for stocks that can actually alert.
    """
    if result.get("_market_cap_checked"):
        return bool(result.get("_market_cap_eligible"))

    ticker = result["ticker"]

    try:
        market_cap = get_market_cap(ticker)
    except Exception as exc:
        market_cap = None
        print(f"[WARN] market cap {ticker} gagal: {exc}")

    eligible = market_cap_is_eligible(market_cap)

    result["market_cap"] = market_cap
    result["_market_cap_checked"] = True
    result["_market_cap_eligible"] = eligible

    if not eligible:
        print(
            f"[MARKET-CAP-PRE-SKIP] {ticker}: "
            f"market_cap={format_market_cap(market_cap)} "
            f"allowed=>{format_market_cap(MIN_MARKET_CAP_IDR)} "
            f"and <{format_market_cap(MAX_MARKET_CAP_IDR)}"
        )

    return eligible


def get_daily_filter_metrics(ticker: str) -> dict:
    cached = get_cached_daily_filter(
        ticker,
        max_age_hours=DAILY_FILTER_CACHE_HOURS,
    )
    if cached is not None:
        return cached

    raw = fetch_stock_history(
        ticker,
        length=STOCK_HISTORY_LENGTH,
    )
    metrics = compute_daily_filter_metrics(raw)
    save_daily_filter(ticker, metrics)
    return metrics


def prepare_daily_filter_candidate(result: dict) -> bool:
    if not DAILY_FILTER_ENABLED:
        return True

    if result.get("_daily_filter_checked"):
        return bool(result.get("_daily_filter_eligible"))

    ticker = result["ticker"]

    try:
        metrics = get_daily_filter_metrics(ticker)
    except Exception as exc:
        metrics = None
        print(f"[WARN] daily filter {ticker} gagal: {exc}")

    eligible = daily_filter_is_eligible(metrics)

    if metrics:
        result["volume_ma_short"] = metrics.get("volume_ma_short")
        result["volume_ma_long"] = metrics.get("volume_ma_long")
        result["volume_ma_ratio"] = metrics.get("volume_ma_ratio")
        result["value_ma"] = metrics.get("value_ma")
        result["history_days"] = metrics.get("history_days")
        result["enough_history"] = metrics.get("enough_history")

    result["_daily_filter_checked"] = True
    result["_daily_filter_eligible"] = eligible

    if not eligible:
        print(
            f"[DAILY-FILTER-SKIP] {ticker}: "
            f"VolMA{VOLUME_MA_SHORT}/MA{VOLUME_MA_LONG}="
            f"{format_num(result.get('volume_ma_ratio'), 'x')} "
            f"need>={VOLUME_MA_RATIO_MIN:.2f}x; "
            f"ValueMA{VALUE_MA_WINDOW}="
            f"{format_rupiah_compact(result.get('value_ma'))} "
            f"need>{format_rupiah_compact(MIN_VALUE_MA_IDR)}; "
            f"history={result.get('history_days', 0)}"
        )

    return eligible


def add_eligible_from_pool(
    selected: list[dict],
    selected_tickers: set[str],
    pool: list[dict],
    limit: int,
) -> int:
    """Fill slots only with price, market-cap, daily, and shareholder-eligible tickers."""
    added = 0

    for result in pool:
        if added >= limit:
            break

        ticker = result["ticker"]
        if ticker in selected_tickers:
            continue

        if not price_is_eligible(result.get("price")):
            print(
                f"[PRICE-PRE-SKIP] {ticker}: "
                f"price={result.get('price')} "
                f"allowed=< {MAX_ALERT_PRICE}"
            )
            continue

        if not prepare_market_cap_candidate(result):
            continue

        if not prepare_daily_filter_candidate(result):
            continue

        if not prepare_shareholder_candidate(result):
            continue

        selected.append(result)
        selected_tickers.add(ticker)
        added += 1

    return added


def format_alert(result: dict) -> str:
    if result["phase"] == "EARLY_IGNITION":
        icon = "🔵"
        title = "EARLY IGNITION"
    elif result["phase"] == "PRE_MOMENTUM":
        icon = "🟠"
        title = "PRE-MOMENTUM WATCH"
    elif result["phase"] == "EARLY_LEAKAGE":
        icon = "🟡"
        title = "EARLY SMART-MONEY LEAKAGE"
    else:
        icon = "🚀"
        title = "POWERFUL MOMENTUM CONFIRMED"

    reasons = "\n".join(
        f"• {x}" for x in result.get("reasons", [])[:5]
    )

    return (
        f"{icon} {title}\n\n"
        f"{result['ticker']} | Price: {result.get('price')}\n"
        f"Score: {result['score']}/100\n"
        f"Leakage: {result['leakage_score']}/100\n"
        f"Momentum: {result['momentum_score']}/100\n"
        f"Ignition: {format_num(result.get('ignition_score'), '/100')}\n"
        f"Market Cap: {format_market_cap(result.get('market_cap'))}\n"
        f"Foreign Score: {format_num(result.get('foreign_score'), '/100')} "
        f"({result.get('foreign_status') or 'UNAVAILABLE'})\n"
        f"Net Foreign Value: {format_rupiah_compact(result.get('net_foreign_value'))}\n"
        f"Net Foreign Ratio: {format_signed_pct(result.get('net_foreign_ratio_pct'))}\n"
        f"Foreign Buy Dominance: "
        f"{format_num(result.get('foreign_buy_dominance_pct'), '%')}\n"
        f"Vol MA10/MA100: {format_num(result.get('volume_ma_ratio'), 'x')}\n"
        f"Value MA5: {format_rupiah_compact(result.get('value_ma'))}\n"
        f"Number of Shareholders: "
        f"{format_shareholder_count(result.get('shareholder_count'))}\n\n"
        f"Daily: {format_num(result.get('daily_change_pct'), '%')}\n"
        f"Interval: {format_num(result.get('interval_return_pct'), '%')}\n"
        f"Volume: {format_num(result.get('volume_spike'), 'x')}\n"
        f"Value: {format_num(result.get('value_spike'), 'x')}\n"
        f"Frequency: {format_num(result.get('frequency_spike'), 'x')}\n"
        f"Breakout: {format_num(result.get('breakout_pct'), '%')}\n"
        f"Microstructure: {format_num(result.get('micro_score'), '/100')}\n"
        f"Aggressive Buy: "
        f"{format_num((result.get('aggressive_buy_ratio') or 0) * 100, '%')}\n"
        f"Bid Dominance: {format_num(result.get('bid_percent'), '%')}\n\n"
        f"{reasons}\n\n"
        "Status: scanner signal, bukan jaminan harga akan naik."
    )


def enrich_quote_from_stock_summary(quote: dict) -> dict:
    """Backfill cumulative activity fields from IDX stock-summary."""
    ticker = str(quote.get("ticker") or "").upper().strip()
    if not ticker:
        return quote

    row = get_stock_summary_row(ticker)
    if not row:
        return quote

    mapping = {
        "volume": "Volume",
        "value": "Value",
        "frequency": "Frequency",
    }

    for target, source in mapping.items():
        if quote.get(target) is not None:
            continue

        raw = row.get(source)
        if raw is None:
            continue

        try:
            quote[target] = float(str(raw).replace(",", "").strip())
        except (TypeError, ValueError):
            pass

    return quote


def collect_quotes(tickers: list[str]) -> list[dict]:
    collected: list[dict] = []

    if USE_MULTI_QUOTE:
        for batch in chunks(tickers, BATCH_SIZE):
            try:
                raw = fetch_multi_quote(batch)
                quotes = extract_quotes(raw)

                if quotes:
                    collected.extend(quotes)
                    continue

                print(
                    f"[WARN] summary tidak dapat dinormalisasi "
                    f"untuk batch {batch[:3]}..."
                )

            except Exception as exc:
                print(f"[WARN] summary gagal: {exc}")

            for ticker in batch:
                try:
                    raw = fetch_quote(ticker)
                    quote = normalize_quote(raw, ticker_hint=ticker)
                    if quote.get("last") is not None:
                        collected.append(quote)
                except Exception as exc:
                    print(f"[ERROR] {ticker}: {exc}")
                    time.sleep(0.2)

    else:
        for ticker in tickers:
            try:
                raw = fetch_quote(ticker)
                quote = normalize_quote(raw, ticker_hint=ticker)
                if quote.get("last") is not None:
                    collected.append(quote)
            except Exception as exc:
                print(f"[ERROR] {ticker}: {exc}")
                time.sleep(0.2)

    result: dict[str, dict] = {}
    for q in collected:
        if q.get("ticker"):
            q = enrich_quote_from_stock_summary(q)
            result[q["ticker"]] = q
    return list(result.values())


def run_once() -> None:
    init_db()

    if not FORCE_RUN and not is_idx_market_window():
        print("Outside IDX regular market window. Scanner skipped.")
        return

    static_tickers = load_universe()

    try:
        active_tickers = discover_active_tickers(TOP_MOVER_COUNT)
        if not active_tickers:
            print("[WARN] dynamic discovery returned 0 ticker")
    except Exception as exc:
        print(f"[WARN] dynamic discovery gagal: {exc}")
        active_tickers = []

    tickers = list(dict.fromkeys(static_tickers + active_tickers))

    print(
        f"Universe static={len(static_tickers)} "
        f"active={len(active_tickers)} "
        f"total={len(tickers)}"
    )
    print(f"Scanning {len(tickers)} ticker...")
    quotes = collect_quotes(tickers)
    print(f"Quote valid: {len(quotes)}")

    if not quotes:
        raise RuntimeError("Tidak ada quote valid. Periksa konfigurasi ZPI/API.")

    for quote in quotes:
        save_snapshot(quote)

    analyzed: list[dict] = []
    evaluation_status: dict[str, str] = {}

    missing_frequency_quotes = sum(
        1 for quote in quotes if quote.get("frequency") is None
    )
    if missing_frequency_quotes:
        print(
            f"[DATA-QUALITY] frequency missing on "
            f"{missing_frequency_quotes}/{len(quotes)} quotes"
        )

    for quote in quotes:
        ticker = quote["ticker"]

        if not price_is_eligible(quote.get("last")):
            evaluation_status[ticker] = "NO_SIGNAL"
            print(
                f"[PRICE-PRE-SKIP] {ticker}: "
                f"price={quote.get('last')} "
                f"allowed=< {MAX_ALERT_PRICE}"
            )
            continue

        result = analyze_ticker(ticker)
        if not result:
            evaluation_status[ticker] = "NOT_EVALUATED"
            continue

        result.update(
            build_foreign_metrics(
                get_stock_summary_row(ticker),
                price=result.get("price"),
            )
        )

        analyzed.append(result)
        evaluation_status[ticker] = "NO_SIGNAL"

        print(
            f"{ticker}: phase={result['phase']} "
            f"score={result['score']} "
            f"vol={result.get('volume_spike')} "
            f"freq={result.get('frequency_spike')}"
        )

    # Build pools before allocating microstructure slots. Tickers that qualify
    # for a pool but lose a slot are PENDING_MICRO, not NO_SIGNAL.
    locked_pool = sorted(
        [x for x in analyzed if x["phase"] != "NONE"],
        key=lambda x: float(x.get("score") or 0),
        reverse=True,
    )

    ignition_pool = sorted(
        [x for x in analyzed if is_ignition_pre_candidate(x)],
        key=lambda x: (
            float(x.get("volume_spike") or 0),
            float(x.get("score") or 0),
        ),
        reverse=True,
    )

    normal_pool = sorted(
        [
            x for x in analyzed
            if (
                x["phase"] != "NONE"
                or float(x.get("score") or 0) >= MICRO_MIN_PRE_SCORE
            )
        ],
        key=lambda x: (
            x["phase"] != "NONE",
            float(x.get("score") or 0),
        ),
        reverse=True,
    )

    potential_tickers = {
        x["ticker"]
        for x in (locked_pool + ignition_pool + normal_pool)
    }
    for ticker in potential_tickers:
        evaluation_status[ticker] = "PENDING_MICRO"

    selected: list[dict] = []
    selected_tickers: set[str] = set()

    ignition_reserve = min(
        max(IGNITION_RESERVED_SLOTS, 0),
        MICRO_MAX_CANDIDATES,
    )
    general_capacity = max(
        0,
        MICRO_MAX_CANDIDATES - ignition_reserve,
    )

    # Existing confirmed/locked signals may use only the non-reserved capacity
    # first, so Ignition genuinely keeps its configured slots.
    add_eligible_from_pool(
        selected,
        selected_tickers,
        locked_pool,
        general_capacity,
    )

    before_ignition = len(selected)
    add_eligible_from_pool(
        selected,
        selected_tickers,
        ignition_pool,
        ignition_reserve,
    )

    for result in selected[before_ignition:]:
        print(
            f"[IGNITION-PRE] {result['ticker']}: "
            f"vol={format_num(result.get('volume_spike'), 'x')} "
            f"interval={format_num(result.get('interval_return_pct'), '%')} "
            f"market_cap={format_market_cap(result.get('market_cap'))}"
        )

    # Reclaim unused Ignition slots, prioritizing remaining locked signals.
    remaining_slots = max(
        0,
        MICRO_MAX_CANDIDATES - len(selected),
    )
    add_eligible_from_pool(
        selected,
        selected_tickers,
        locked_pool,
        remaining_slots,
    )

    remaining_slots = max(
        0,
        MICRO_MAX_CANDIDATES - len(selected),
    )
    add_eligible_from_pool(
        selected,
        selected_tickers,
        normal_pool,
        remaining_slots,
    )

    # Hard-filter rejects are genuine NO_SIGNAL for Bot 4. Candidates that
    # merely lost a microstructure slot remain PENDING_MICRO.
    for result in analyzed:
        ticker = result["ticker"]

        if (
            result.get("_market_cap_checked")
            and not result.get("_market_cap_eligible")
        ):
            evaluation_status[ticker] = "NO_SIGNAL"

        if (
            result.get("_daily_filter_checked")
            and not result.get("_daily_filter_eligible")
        ):
            evaluation_status[ticker] = "NO_SIGNAL"

        if (
            result.get("_shareholder_checked")
            and not result.get("_shareholder_eligible")
        ):
            evaluation_status[ticker] = "NO_SIGNAL"

    print(
        f"Microstructure candidates: {len(selected)} "
        f"(max={MICRO_MAX_CANDIDATES}, "
        f"ignition_reserved={ignition_reserve})"
    )

    candidates: list[dict] = []

    for result in selected:
        ticker = result["ticker"]

        try:
            micro = fetch_microstructure(ticker)
            result = apply_micro_confirmation(result, micro)
        except Exception as exc:
            evaluation_status[ticker] = "API_FAILED"
            print(
                f"[MICRO-FAILED] {ticker}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        print(
            f"[MICRO] {ticker}: "
            f"micro={result.get('micro_score')} "
            f"buy={format_num((result.get('aggressive_buy_ratio') or 0) * 100, '%')} "
            f"bid={format_num(result.get('bid_percent'), '%')} "
            f"trades={result.get('trade_count')}/"
            f"{result.get('raw_trade_count')} "
            f"latest_age={format_num(result.get('latest_trade_age_minutes'), 'm')} "
            f"phase={result['phase']} "
            f"score={result['score']}"
        )

        if result["phase"] == "NONE":
            evaluation_status[ticker] = "NO_SIGNAL"
        else:
            evaluation_status[ticker] = "SIGNAL"
            candidates.append(result)

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    eligible_candidates: list[dict] = []

    for result in candidates:
        ticker = result["ticker"]

        if not price_is_eligible(result.get("price")):
            evaluation_status[ticker] = "NO_SIGNAL"
            print(
                f"[PRICE-SKIP] {ticker}: "
                f"price={result.get('price')} "
                f"allowed=< {MAX_ALERT_PRICE}"
            )
            continue

        if not market_cap_is_eligible(result.get("market_cap")):
            evaluation_status[ticker] = "NO_SIGNAL"
            print(
                f"[MARKET-CAP-SKIP] {ticker}: "
                f"market_cap={format_market_cap(result.get('market_cap'))}"
            )
            continue

        if DAILY_FILTER_ENABLED and not daily_filter_is_eligible(
            {
                "volume_ma_ratio": result.get("volume_ma_ratio"),
                "value_ma": result.get("value_ma"),
                "history_days": result.get("history_days"),
                "enough_history": result.get("enough_history"),
            }
        ):
            evaluation_status[ticker] = "NO_SIGNAL"
            print(f"[DAILY-FILTER-FINAL-SKIP] {ticker}")
            continue

        if SHAREHOLDER_FILTER_ENABLED and not shareholder_is_eligible(
            result.get("shareholder_count")
        ):
            evaluation_status[ticker] = "NO_SIGNAL"
            print(
                f"[SHAREHOLDER-FINAL-SKIP] {ticker}: "
                f"holders={result.get('shareholder_count')}"
            )
            continue

        eligible_candidates.append(result)
        evaluation_status[ticker] = "SIGNAL"

    # Re-arm only when a genuine no-signal observation exists. Preserve state
    # for candidates awaiting microstructure, insufficient baseline data, and
    # API failures.
    for ticker, status in evaluation_status.items():
        if status == "NO_SIGNAL":
            mark_no_signal(ticker)

    status_counts: dict[str, int] = {}
    for status in evaluation_status.values():
        status_counts[status] = status_counts.get(status, 0) + 1

    print(
        "[EVAL-STATUS] "
        + " ".join(
            f"{key}={value}"
            for key, value in sorted(status_counts.items())
        )
    )

    for result in eligible_candidates:
        ticker = result["ticker"]

        if should_alert(result):
            signal_event_id = _OBS.record_signal(
                signal_type=result["phase"],
                entity=ticker,
                score=result.get("score"),
                status="alert_candidate",
                payload={
                    "price": result.get("price"),
                    "foreign_score": result.get("foreign_score"),
                    "foreign_status": result.get("foreign_status"),
                    "micro_score": result.get("micro_score"),
                    "daily_change_pct": result.get("daily_change_pct"),
                    "interval_return_pct": result.get("interval_return_pct"),
                    "shareholder_count": result.get("shareholder_count"),
                },
            )
            delivery = send_telegram(format_alert(result))
            delivery_ok = bool(delivery.get("ok"))
            _OBS.record_delivery(
                channel="telegram",
                status="success" if delivery_ok else "failed",
                signal_event_id=signal_event_id,
                destination="configured-telegram-targets",
                metadata={
                    "sent": delivery.get("sent", 0),
                    "failed": delivery.get("failed", 0),
                    "signal_type": result["phase"],
                    "ticker": ticker,
                },
            )

            if delivery_ok:
                mark_alerted(result)
                print(
                    f"[ALERT] {ticker} "
                    f"{result['phase']} {result['score']}"
                )
            else:
                print(
                    f"[WARN] Telegram gagal; state alert {ticker} "
                    "tidak dinaikkan."
                )
        else:
            mark_signal_present(ticker)
            print(
                f"[NO-REPEAT] {ticker} "
                f"{result['phase']} {result['score']}"
            )


if __name__ == "__main__":
    run_once()
