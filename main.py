from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from bot.notifier import send_telegram
from scanner.top_gainer import NeonDailyDelivery, send_daily_top_gainers
from scanner.top_gainer_potential import POTENTIAL_NAMESPACE, send_daily_potential
from datetime import datetime
from scanner.ptsn_structure import build_ptsn_structure, format_ptsn_structure
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
    FILTER2_ENABLED,
    TOP_GAINER_ENABLED,
    TOP_GAINER_COUNT,
    TOP_GAINER_MIN_COVERAGE,
    TOP_GAINER_SEND_AFTER_HOUR,
    TOP_GAINER_TARGET,
    TOP_GAINER_POTENTIAL_ENABLED,
    TOP_GAINER_POTENTIAL_COUNT,
    TOP_GAINER_POTENTIAL_SEND_AFTER_HOUR,
    PTSN_STRUCTURE_ENABLED,
    PTSN_STRUCTURE_COOLDOWN_MINUTES,
    PTSN_STRUCTURE_MAX_ALERTS_PER_CYCLE,
    FILTER2_RESERVED_SLOTS,
    FILTER2_MIN_PRE_SCORE,
    FILTER2_DAILY_VOLUME_MA_RATIO_MIN,
    FILTER2_IGNITION_MIN_VOLUME_SPIKE,
    FILTER2_ALERT_COOLDOWN_MINUTES,
    MARKET_CAP_FILTER_ENABLED,
    MIN_MARKET_CAP_IDR,
    MAX_MARKET_CAP_IDR,
    MARKET_CAP_CACHE_HOURS,
    MAX_ALERT_PRICE,
    DAILY_FILTER_ENABLED,
    DAILY_FILTER_CACHE_HOURS,
    STOCK_HISTORY_LENGTH,
    RECENT_SNAPSHOTS,
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
    FILTER1_VALUE_MA50_ENABLED,
    FILTER1_VALUE_MA50_WINDOW,
    FILTER1_MIN_VALUE_MA50_IDR,
    FILTER1_TOP_N_ALERTS_PER_CYCLE,
    QUALITY_SCORE_ENABLED,
    ENTRY_SETUP_ENABLED,
    REVERSAL_LIVE_ENABLED,
    EOD_ENTRY_ENGINE_ENABLED,
    EOD_ENTRY_LIVE_PLAN_LIMIT,
    REVERSAL_WATCH_MINUTES,
    REVERSAL_MAX_WATCHES,
    TOP_MOVER_COUNT,
    USE_MULTI_QUOTE,
    TRADEFLOW_INTERNAL_ENABLED,
    TRADEFLOW_TREND_SNAPSHOTS,
    TRADEFLOW_TREND_MIN_SNAPSHOTS,
    TRADEFLOW_TREND_FLAT_TOLERANCE_PCT,
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
    select_next_primary_phase,
    should_alert,
)
from scanner.market_data import (
    collect_shared_market_snapshot,
    discover_active_tickers,
    extract_market_cap,
    fetch_multi_quote,
    fetch_quote,
    get_stock_summary_row,
    fetch_stock_history,
    market_cache_backend,
)
from scanner.market_hours import is_idx_market_window
from scanner.shareholder_filter import fetch_latest_shareholder_count
from scanner.tradeflow import build_tradeflow_layers
from scanner.quality import build_quality_metrics
from scanner.liquidity_observer import build_dynamic_liquidity_observation
from scanner.entry_setup import build_pullback_reversal_entry
from scanner.reversal_live import evaluate_live_reversal_watch
from scanner.eod_plan_live import (
    load_eod_entry_plans,
    eod_plan_universe_tickers,
    seed_eod_entry_watches,
    prioritize_reversal_watches,
)
from scanner.microstructure import (
    apply_micro_confirmation,
    apply_relaxed_micro_confirmation,
    fetch_microstructure,
    relaxed_anti_falling_knife_pass,
)
from scanner.normalizer import extract_quotes, normalize_quote
from storage.db import (
    get_cached_daily_filter,
    get_cached_shareholder_filter,
    get_recent_snapshots,
    get_cached_market_cap,

    init_db,
    save_daily_filter,
    save_market_cap,
    save_shareholder_filter,
    save_snapshot,
    save_tradeflow_snapshot,
    get_recent_tradeflow_snapshots,
    recent_screener_alert_exists,
    save_screener_alert,
    upsert_reversal_watch,
    ensure_reversal_watch,
    get_active_reversal_watches,
    get_recent_alert_seeds,
    cancel_filter2_reversal_watches,
    update_reversal_watch,
    now_wib_iso,
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


def is_filter2_pre_candidate(result: dict) -> bool:
    """Relaxed Filter 1 candidate gate with hard falling-knife protection."""
    if not relaxed_anti_falling_knife_pass(result):
        return False

    score = float(result.get("score") or 0)
    volume_spike = float(result.get("volume_spike") or 0)
    activity_spike = float(result.get("activity_spike") or 0)

    return (
        score >= FILTER2_MIN_PRE_SCORE
        or volume_spike >= FILTER2_IGNITION_MIN_VOLUME_SPIKE
        or activity_spike >= 1.20
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


def _format_rupiah_indonesia(value, *, signed: bool = False) -> str:
    if value is None:
        return "-"

    number = float(value)
    amount = abs(number)
    if signed:
        sign = "+" if number > 0 else "-" if number < 0 else ""
    else:
        sign = "-" if number < 0 else ""

    def dec(raw: float) -> str:
        return f"{raw:.2f}".replace(".", ",")

    if amount >= 1e12:
        return f"Rp {sign}{dec(amount / 1e12)} Triliun"
    if amount >= 1e9:
        return f"Rp {sign}{dec(amount / 1e9)} Miliar"
    if amount >= 1e6:
        return f"Rp {sign}{dec(amount / 1e6)} Juta"
    if amount >= 1e3:
        return f"Rp {sign}{dec(amount / 1e3)} Ribu"
    return f"Rp {sign}{amount:,.0f}"


def format_market_cap(value) -> str:
    return _format_rupiah_indonesia(value, signed=False)


def format_rupiah_compact(value) -> str:
    """Gross/non-net rupiah amount: positive values have no '+' prefix."""
    return _format_rupiah_indonesia(value, signed=False)


def format_rupiah_signed(value) -> str:
    """Net/directional rupiah amount: preserve + / - direction."""
    return _format_rupiah_indonesia(value, signed=True)


def format_price(value) -> str:
    if value is None:
        return "-"
    number = float(value)
    if number.is_integer():
        return f"Rp{number:,.0f}"
    return f"Rp{number:,.2f}"


def format_golden_pocket(result: dict, suffix: str = "") -> str:
    def metric_key(name: str) -> str:
        return f"{name}_{suffix}" if suffix else name

    lower = result.get(metric_key("golden_pocket_lower"))
    upper = result.get(metric_key("golden_pocket_upper"))
    price = result.get("price")
    default_lookback = 5 if suffix == "5d" else 20
    lookback = (
        result.get(metric_key("golden_pocket_lookback"))
        or default_lookback
    )
    scope = "TACTICAL" if suffix == "5d" else "STRUCTURAL"
    section_icon = "🟡" if suffix == "5d" else "🟠"
    heading = f"{section_icon} GOLDEN POCKET — {scope} {lookback}D"

    if lower is None or upper is None:
        return (
            f"{heading}\n"
            "Fib Ratio: 0.618–0.650\n"
            "Zone: -\n"
            "Position: -\n"
            "Swing: -\n"
            "Period: -"
        )

    lower = float(lower)
    upper = float(upper)

    if price is None:
        position = "-"
    else:
        price = float(price)
        if lower <= price <= upper:
            position = "IN ZONE ✅"
        elif price > upper:
            distance = ((price - upper) / upper * 100) if upper else 0.0
            position = f"ABOVE +{distance:.2f}%"
        else:
            distance = ((lower - price) / price * 100) if price else 0.0
            position = f"BELOW {distance:.2f}% TO ZONE"

    swing_low = result.get(metric_key("swing_low"))
    swing_high = result.get(metric_key("swing_high"))
    low_date = result.get(metric_key("swing_low_date")) or "-"
    high_date = result.get(metric_key("swing_high_date")) or "-"

    return (
        f"{heading}\n"
        "Fib Ratio: 0.618–0.650\n"
        f"Zone: {format_price(lower)} – {format_price(upper)}\n"
        f"Position: {position}\n"
        f"Swing: {format_price(swing_low)} → {format_price(swing_high)}\n"
        f"Period: {low_date} → {high_date}"
    )


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
    if (
        cached is not None
        and (
            not FILTER1_VALUE_MA50_ENABLED
            or cached.get("transaction_value_ma50") is not None
        )
        and cached.get("golden_pocket_lookback") == 20
        and cached.get("golden_pocket_lookback_5d") == 5
        and cached.get("swing_low") not in (0, 0.0)
        and cached.get("swing_high") not in (0, 0.0)
        and cached.get("swing_low_5d") not in (0, 0.0)
        and cached.get("swing_high_5d") not in (0, 0.0)
    ):
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
        result["transaction_value_ma50"] = metrics.get(
            "transaction_value_ma50"
        )
        result["history_days"] = metrics.get("history_days")
        result["enough_history"] = metrics.get("enough_history")
        result["swing_low"] = metrics.get("swing_low")
        result["swing_high"] = metrics.get("swing_high")
        result["swing_low_date"] = metrics.get("swing_low_date")
        result["swing_high_date"] = metrics.get("swing_high_date")
        result["golden_pocket_lower"] = metrics.get("golden_pocket_lower")
        result["golden_pocket_upper"] = metrics.get("golden_pocket_upper")
        result["golden_pocket_lookback"] = metrics.get(
            "golden_pocket_lookback"
        )
        result["swing_low_5d"] = metrics.get("swing_low_5d")
        result["swing_high_5d"] = metrics.get("swing_high_5d")
        result["swing_low_date_5d"] = metrics.get("swing_low_date_5d")
        result["swing_high_date_5d"] = metrics.get("swing_high_date_5d")
        result["golden_pocket_lower_5d"] = metrics.get(
            "golden_pocket_lower_5d"
        )
        result["golden_pocket_upper_5d"] = metrics.get(
            "golden_pocket_upper_5d"
        )
        result["golden_pocket_lookback_5d"] = metrics.get(
            "golden_pocket_lookback_5d"
        )

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
            f"ValueMA{FILTER1_VALUE_MA50_WINDOW}="
            f"{format_rupiah_compact(result.get('transaction_value_ma50'))} "
            f"need>={format_rupiah_compact(FILTER1_MIN_VALUE_MA50_IDR)}; "
            f"history={result.get('history_days', 0)}"
        )

    return eligible


def prepare_relaxed_daily_filter_candidate(result: dict) -> bool:
    """Filter 2 liquidity gate: same daily context, lower volume-MA threshold."""
    if not DAILY_FILTER_ENABLED:
        return True

    if result.get("_filter2_daily_checked"):
        return bool(result.get("_filter2_daily_eligible"))

    ticker = result["ticker"]
    try:
        metrics = get_daily_filter_metrics(ticker)
    except Exception as exc:
        metrics = None
        print(f"[WARN] Filter 2 daily filter {ticker} gagal: {exc}")

    if metrics:
        for key in (
            "volume_ma_short", "volume_ma_long", "volume_ma_ratio", "value_ma",
            "transaction_value_ma50", "history_days", "enough_history",
            "swing_low", "swing_high",
            "swing_low_date", "swing_high_date", "golden_pocket_lower",
            "golden_pocket_upper", "golden_pocket_lookback", "swing_low_5d",
            "swing_high_5d", "swing_low_date_5d", "swing_high_date_5d",
            "golden_pocket_lower_5d", "golden_pocket_upper_5d",
            "golden_pocket_lookback_5d",
        ):
            result[key] = metrics.get(key)

    ratio = metrics.get("volume_ma_ratio") if metrics else None
    value_ma = metrics.get("value_ma") if metrics else None
    enough = bool(metrics and metrics.get("enough_history"))
    eligible = bool(
        enough
        and ratio is not None
        and value_ma is not None
        and float(ratio) >= FILTER2_DAILY_VOLUME_MA_RATIO_MIN
        and float(value_ma) > MIN_VALUE_MA_IDR
    )

    result["_filter2_daily_checked"] = True
    result["_filter2_daily_eligible"] = eligible

    if not eligible:
        print(
            f"[FILTER2-DAILY-SKIP] {ticker}: "
            f"VolMA{VOLUME_MA_SHORT}/MA{VOLUME_MA_LONG}="
            f"{format_num(ratio, 'x')} need>={FILTER2_DAILY_VOLUME_MA_RATIO_MIN:.2f}x; "
            f"ValueMA{VALUE_MA_WINDOW}={format_rupiah_compact(value_ma)} "
            f"need>{format_rupiah_compact(MIN_VALUE_MA_IDR)}"
        )

    return eligible


def add_eligible_from_pool(
    selected: list[dict],
    selected_tickers: set[str],
    pool: list[dict],
    limit: int,
    screener_tag: str = "LEGACY",
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

        daily_eligible = prepare_daily_filter_candidate(result)
        record_dynamic_liquidity_observation(
            result,
            lane=f"FILTER1_{screener_tag}",
        )
        if not daily_eligible:
            continue

        if not prepare_shareholder_candidate(result):
            continue

        tags = set(result.get("_selected_by") or [])
        tags.add(screener_tag)
        result["_selected_by"] = sorted(tags)
        selected.append(result)
        selected_tickers.add(ticker)
        added += 1

    return added



def prepare_shareholder_candidate_from_cache(ticker: str) -> bool:
    """Respect enabled shareholder hard gate without requesting extra APIs."""
    cached = get_cached_shareholder_filter(
        ticker,
        success_max_age_hours=SHAREHOLDER_CACHE_HOURS,
        failure_max_age_hours=SHAREHOLDER_FAILURE_CACHE_HOURS,
    )
    if not cached:
        return False
    return shareholder_is_eligible(cached.get("holder_count"))


def record_dynamic_liquidity_observation(
    result: dict,
    *,
    lane: str,
) -> bool:
    """Persist the dynamic-liquidity idea to Neon without affecting signals."""
    observation = build_dynamic_liquidity_observation(result, lane=lane)
    if observation is None:
        return False

    ticker = str(result.get("ticker") or "").upper().strip()
    if not ticker:
        return False

    event_id = f"dynamic-liquidity:{_OBS.run_id}:{ticker}"
    _OBS.record_signal(
        signal_type="DYNAMIC_LIQUIDITY_MATCH",
        entity=ticker,
        score=result.get("score"),
        status="observed_only",
        payload=observation,
        event_id=event_id,
    )
    print(
        f"[DYNAMIC-LIQUIDITY-OBS] {ticker}: "
        f"lane={lane} "
        f"cap={format_market_cap(observation.get('market_cap'))} "
        f"ma50={format_rupiah_compact(observation.get('transaction_value_ma50'))} "
        f"need>={format_rupiah_compact(observation.get('dynamic_threshold'))} "
        f"turnover={format_num(observation.get('ma50_to_market_cap_pct'), '%')}"
    )
    return True


def should_run_filter2_fallback(primary_signal_count: int) -> bool:
    """Run relaxed Filter 2 only when Filter 1 has zero final valid candidates."""
    return (
        FILTER2_ENABLED
        and FILTER2_RESERVED_SLOTS > 0
        and int(primary_signal_count) == 0
    )


def enrich_micro_with_tradeflow(
    ticker: str,
    micro: dict,
    price: float | None,
) -> dict:
    """Persist and classify internal tradeflow data without changing signal rules."""
    if not TRADEFLOW_INTERNAL_ENABLED:
        return micro

    save_tradeflow_snapshot(
        ticker,
        micro,
        price=price,
    )
    history = get_recent_tradeflow_snapshots(
        ticker,
        limit=TRADEFLOW_TREND_SNAPSHOTS,
    )
    layers = build_tradeflow_layers(
        micro,
        history,
        min_samples=TRADEFLOW_TREND_MIN_SNAPSHOTS,
        flat_tolerance_pct=TRADEFLOW_TREND_FLAT_TOLERANCE_PCT,
    )
    output = {**micro, **layers}

    print(
        f"[TRADEFLOW] {ticker}: "
        f"buy={format_rupiah_compact(output.get('aggressive_buy_value'))} "
        f"sell={format_rupiah_compact(output.get('aggressive_sell_value'))} "
        f"net={format_rupiah_signed(output.get('net_aggression_value'))} "
        f"trend={output.get('aggression_trend')} "
        f"samples={output.get('aggression_trend_samples')}"
    )
    return output


def enrich_result_with_quality(result: dict) -> dict:
    """Attach Bot 4 V2 soft quality metrics after microstructure confirmation."""
    if not QUALITY_SCORE_ENABLED:
        output = dict(result)
        output.setdefault("quality_score", float(result.get("score") or 0.0))
        return output
    return {**result, **build_quality_metrics(result)}


def _quality_sort_key(result: dict) -> tuple[float, float, int, str]:
    phase_rank = {
        "EARLY_IGNITION": 1,
        "PRE_MOMENTUM": 2,
        "POWERFUL_MOMENTUM": 3,
    }.get(str(result.get("phase") or ""), 0)
    return (
        float(result.get("quality_score") or 0.0),
        float(result.get("score") or 0.0),
        phase_rank,
        str(result.get("ticker") or ""),
    )


def rank_top_n_candidates(
    candidates: list[dict],
    limit: int,
) -> tuple[list[dict], list[dict]]:
    ranked = sorted(candidates, key=_quality_sort_key, reverse=True)
    safe_limit = max(0, int(limit))
    return ranked[:safe_limit], ranked[safe_limit:]


def _trend_label(value) -> str:
    return {
        "RISING": "RISING ↗",
        "FALLING": "FALLING ↘",
        "FLAT": "FLAT →",
        "MIXED": "MIXED ↕",
        "INSUFFICIENT_HISTORY": "WAIT DATA",
    }.get(str(value or ""), "-")


def _format_entry_setup(result: dict) -> str:
    if not ENTRY_SETUP_ENABLED:
        return ""

    eod_plan = result.get("eod_entry_plan")
    if isinstance(eod_plan, dict):
        return (
            f"🌙 EOD ENTRY PLAN — {eod_plan.get('setup_type') or 'SETUP'}\n"
            f"• EOD Date: {eod_plan.get('trade_date') or '-'} "
            f"| Score: {format_num(eod_plan.get('eod_score'), '/100')}\n"
            f"• Entry Zone: "
            f"{format_price(eod_plan.get('entry_zone_lower'))}–"
            f"{format_price(eod_plan.get('entry_zone_upper'))}\n"
            f"• Live Trigger: reclaim ≥ "
            f"{format_price(eod_plan.get('reversal_trigger'))}\n"
            f"• Invalidation: < "
            f"{format_price(eod_plan.get('invalidation_price'))}\n"
            "• Live Confirm: tunggu matched-flow (CVD, buyer ratio, "
            "absorption, bid) sebelum entry"
        )

    setup = build_pullback_reversal_entry(result)
    if setup is None:
        return ""

    base = setup.get("breakout_base")
    base_text = (
        f" | Base {format_price(base)}"
        if base is not None
        else ""
    )

    return (
        "🎯 ENTRY REVERSAL / PULLBACK — LIVE FALLBACK\n"
        f"• Pullback Area: "
        f"{format_price(setup.get('pullback_lower'))}–"
        f"{format_price(setup.get('pullback_upper'))}\n"
        f"• Reversal Trigger: reclaim ≥ "
        f"{format_price(setup.get('reversal_trigger'))}{base_text}\n"
        f"• Buyer Confirm: {setup.get('entry_confirmation')}\n"
        f"• Invalidation: < {format_price(setup.get('invalidation_price'))} "
        f"atau selling pressure membesar\n"
        "• Eksekusi: fallback intraday; tunggu buyer kembali dominan"
    )


def _phase_title(phase: str | None) -> tuple[str, str]:
    return {
        "EARLY_IGNITION": ("🔵", "EARLY IGNITION"),
        "PRE_MOMENTUM": ("🟠", "PRE-MOMENTUM"),
        "EARLY_LEAKAGE": ("🟡", "EARLY SMART-MONEY LEAKAGE"),
        "POWERFUL_MOMENTUM": ("🚀", "POWERFUL MOMENTUM"),
    }.get(phase or "", ("📡", phase or "MOMENTUM"))


def format_alert(
    result: dict,
    rank: int | None = None,
    lane: str | None = None,
) -> str:
    icon, title = _phase_title(result.get("phase"))
    lane_name = lane or (
        "FILTER 2 — RELAXED"
        if result.get("filter2_relaxed")
        else "FILTER 1 — STRICT"
    )
    rank_text = f" | TOP #{rank}" if rank is not None else ""

    ma50_rule = (
        f"(referensi F1 min {format_rupiah_compact(FILTER1_MIN_VALUE_MA50_IDR)})"
        if result.get("filter2_relaxed")
        else f"(min {format_rupiah_compact(FILTER1_MIN_VALUE_MA50_IDR)})"
    )

    absorption = str(result.get("selling_absorption") or "NONE")
    absorption_icon = {
        "CONFIRMED": "✅",
        "WATCH": "🟡",
        "NONE": "—",
    }.get(absorption, "—")

    reasons = "\n".join(
        f"• {x}" for x in result.get("reasons", [])[:3]
    )
    reasons_section = (
        f"\n\n🧭 ALASAN SINYAL\n{reasons}"
        if reasons
        else ""
    )

    return (
        f"{icon} BOT 4 — {title}\n"
        f"{lane_name}{rank_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 {result['ticker']} | {format_price(result.get('price'))}\n"
        f"⭐ Quality: "
        f"{format_num(result.get('quality_score'), '/100') if result.get('quality_score') is not None else 'N/A (data historis pre-V2)'} "
        f"| Momentum: {format_num(result.get('score'), '/100')}\n\n"

        f"{_format_entry_setup(result)}\n\n"

        "💧 LIKUIDITAS\n"
        f"• Tx Value MA50: "
        f"{format_rupiah_compact(result.get('transaction_value_ma50'))} "
        f"{ma50_rule}\n"
        f"• Value MA5: {format_rupiah_compact(result.get('value_ma'))}\n"
        f"• Vol MA10/MA100: {format_num(result.get('volume_ma_ratio'), 'x')}\n"
        f"• Market Cap: {format_market_cap(result.get('market_cap'))}\n\n"

        "🧠 SMART MONEY / TRADEFLOW\n"
        f"• Agg Buy: {format_rupiah_compact(result.get('aggressive_buy_value'))}\n"
        f"• Agg Sell: {format_rupiah_compact(result.get('aggressive_sell_value'))}\n"
        f"• Net Aggression: "
        f"{format_rupiah_signed(result.get('net_aggression_value'))}\n"
        f"• Aggression Trend: {_trend_label(result.get('aggression_trend'))}\n"
        f"• Selling Absorption: {absorption} {absorption_icon}\n"
        f"• Agg Buy Ratio: "
        f"{format_num((result.get('aggressive_buy_ratio') or 0) * 100, '%')}\n"
        f"• Bid Dominance: {format_num(result.get('bid_percent'), '%')}\n"
        f"• Microstructure: {format_num(result.get('micro_score'), '/100')}\n\n"

        "⚡ MOMENTUM\n"
        f"• Daily: {format_signed_pct(result.get('daily_change_pct'))}\n"
        f"• Interval: {format_signed_pct(result.get('interval_return_pct'))}\n"
        f"• Volume / Value / Freq: "
        f"{format_num(result.get('volume_spike'), 'x')} / "
        f"{format_num(result.get('value_spike'), 'x')} / "
        f"{format_num(result.get('frequency_spike'), 'x')}\n"
        f"• Breakout: {format_signed_pct(result.get('breakout_pct'))}\n"
        f"• Leakage / Momentum / Ignition: "
        f"{format_num(result.get('leakage_score'), '')} / "
        f"{format_num(result.get('momentum_score'), '')} / "
        f"{format_num(result.get('ignition_score'), '')}\n\n"

        "🌍 FOREIGN FLOW\n"
        f"• Score: {format_num(result.get('foreign_score'), '/100')}\n"
        f"• Status: {result.get('foreign_status') or 'UNAVAILABLE'}\n"
        f"• Net Value: {format_rupiah_signed(result.get('net_foreign_value'))}\n"
        f"• Net Ratio: {format_signed_pct(result.get('net_foreign_ratio_pct'))}\n"
        f"• Buy Dominance: "
        f"{format_num(result.get('foreign_buy_dominance_pct'), '%')}"
        f"{reasons_section}\n\n"
        "⚠️ Scanner signal — tetap gunakan risk management."
    )


def format_filter2_alert(result: dict, rank: int) -> str:
    return format_alert(
        result,
        rank=rank,
        lane="FILTER 2 — RELAXED FALLBACK",
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



def register_reversal_watch_from_result(result: dict, *, lane: str, target: str) -> bool:
    if not REVERSAL_LIVE_ENABLED:
        return False
    setup = build_pullback_reversal_entry(result)
    ticker = str(result.get("ticker") or "").upper().strip()
    if not ticker or setup is None:
        return False
    upsert_reversal_watch(
        ticker=ticker,
        source_lane=lane,
        source_phase=result.get("phase"),
        target=target,
        alert_price=float(setup["alert_price"]),
        pullback_lower=float(setup["pullback_lower"]),
        pullback_upper=float(setup["pullback_upper"]),
        reversal_trigger=float(setup["reversal_trigger"]),
        invalidation_price=float(setup["invalidation_price"]),
    )
    print(
        f"[REVERSAL-WATCH-ARMED] {ticker}: lane={lane} "
        f"alert={format_price(setup['alert_price'])} "
        f"pullback={format_price(setup['pullback_lower'])}-"
        f"{format_price(setup['pullback_upper'])}"
    )
    return True


def bootstrap_reversal_watches_from_recent_alerts() -> int:
    if not REVERSAL_LIVE_ENABLED:
        return 0

    limit = max(REVERSAL_MAX_WATCHES * 3, 10)
    seeds = list(
        get_recent_alert_seeds(
            max_age_minutes=REVERSAL_WATCH_MINUTES,
            limit=limit,
        )
    )

    # Recover alert candidates from Neon too. This covers partial Telegram
    # deliveries where private succeeded but group failed, so SQLite cooldown
    # state was intentionally not committed (e.g. BEKS 10:06).
    if _OBS.enabled:
        try:
            with _OBS._connect() as con:
                with con.cursor() as cur:
                    cur.execute(
                        """
                        SELECT signal_type, entity, occurred_at, payload
                        FROM project_fabric.signal_events
                        WHERE service = %s
                          AND status = 'alert_candidate'
                          AND signal_type = ANY(%s)
                          AND occurred_at >= now() - (%s * interval '1 minute')
                        ORDER BY occurred_at DESC
                        LIMIT %s
                        """,
                        (
                            "cuankus-momentum",
                            [
                                "EARLY_IGNITION",
                                "PRE_MOMENTUM",
                                "POWERFUL_MOMENTUM",
                            ],
                            REVERSAL_WATCH_MINUTES,
                            limit,
                        ),
                    )
                    rows = cur.fetchall()
            for signal_type, entity, occurred_at, payload in rows:
                payload = payload if isinstance(payload, dict) else {}
                price = payload.get("price")
                if entity and price is not None:
                    raw_type = str(signal_type)
                    lane = "FILTER2" if raw_type.startswith("FILTER2_") else "FILTER1"
                    phase = (
                        raw_type[len("FILTER2_"):]
                        if raw_type.startswith("FILTER2_")
                        else raw_type
                    )
                    seeds.append(
                        {
                            "ticker": str(entity).upper(),
                            "ts": occurred_at.isoformat(),
                            "phase": phase,
                            "price": price,
                            "lane": lane,
                        }
                    )
        except Exception as exc:
            print(
                f"[REVERSAL-BOOTSTRAP-NEON-WARN] "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )

    seeds.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    created = 0
    seen: set[str] = set()
    for seed in seeds:
        ticker = str(seed.get("ticker") or "").upper()
        price = seed.get("price")
        if not ticker or price is None or ticker in seen:
            continue
        seen.add(ticker)

        setup = build_pullback_reversal_entry({"ticker": ticker, "price": price})
        if setup is None:
            continue
        if ensure_reversal_watch(
            ticker=ticker,
            source_lane=str(seed.get("lane") or "LEGACY_ALERT"),
            source_phase=seed.get("phase"),
            target="private" if ticker == "PTSN" else "all",
            alert_price=float(setup["alert_price"]),
            pullback_lower=float(setup["pullback_lower"]),
            pullback_upper=float(setup["pullback_upper"]),
            reversal_trigger=float(setup["reversal_trigger"]),
            invalidation_price=float(setup["invalidation_price"]),
            alert_ts=str(seed["ts"]),
        ):
            created += 1
            print(
                f"[REVERSAL-WATCH-BOOTSTRAP] {ticker}: "
                f"source={seed.get('lane')} alert_ts={seed.get('ts')}"
            )
    return created

def format_live_reversal_confirmed(result: dict) -> str:
    delta_flip = "YES ✅" if result.get("delta_flip") else "NO"
    absorption = "CONFIRMED ✅" if result.get("selling_absorption_live") else "WATCH"
    source_lane = str(result.get("source_lane") or "POST-ALERT WATCH")
    setup_origin = (
        "EOD PLAN + LIVE CONFIRMATION"
        if source_lane.startswith("EOD_")
        else "LIVE MOMENTUM FALLBACK"
    )
    return (
        "✅ BOT 4 — ENTRY CONFIRMED\n"
        f"{setup_origin}\n"
        f"{source_lane}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 {result['ticker']} | {format_price(result.get('current_price'))}\n"
        f"⭐ Live Reversal Score: {format_num(result.get('score'), '/100')}\n\n"
        "🎯 PRICE STRUCTURE\n"
        f"• Alert Price: {format_price(result.get('alert_price'))}\n"
        f"• Pullback Area: {format_price(result.get('pullback_lower'))}–"
        f"{format_price(result.get('pullback_upper'))}\n"
        f"• Pullback Low Seen: {format_price(result.get('low_seen'))}\n"
        f"• Reclaim Trigger: ≥ {format_price(result.get('reversal_trigger'))} ✅\n"
        f"• Invalidation: < {format_price(result.get('invalidation_price'))}\n\n"
        "📊 MATCHED TRADES SEJAK PULLBACK\n"
        f"• Agg Buy: {format_rupiah_compact(result.get('cumulative_buy_value'))}\n"
        f"• Agg Sell: {format_rupiah_compact(result.get('cumulative_sell_value'))}\n"
        f"• CVD / Net Aggression: {format_rupiah_signed(result.get('cumulative_delta'))}\n"
        f"• Buyer Ratio: {format_num((result.get('cumulative_buy_ratio') or 0) * 100, '%')}\n"
        f"• New Batch Delta: {format_rupiah_signed(result.get('batch_delta'))}\n"
        f"• Delta Flip: {delta_flip}\n"
        f"• Selling Absorption: {absorption}\n"
        f"• Bid Dominance: {format_num(result.get('bid_percent'), '%')}\n"
        f"• Matched Trades: {int(result.get('cumulative_trade_count') or 0)}\n\n"
        "🟢 STATUS: BUYER TAKEOVER + PRICE RECLAIM\n"
        "Entry terkonfirmasi setelah transaksi matched mendukung reversal.\n"
        "⚠️ Bukan auto-order; tetap gunakan risk management."
    )


def format_live_reversal_cancelled(result: dict, watch: dict) -> str:
    return (
        "❌ BOT 4 — PULLBACK FAILED / SETUP CANCELLED\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 {watch['ticker']} | {format_price(result.get('current_price'))}\n"
        f"• Alert Price: {format_price(watch.get('alert_price'))}\n"
        f"• Pullback Area: {format_price(watch.get('pullback_lower'))}–"
        f"{format_price(watch.get('pullback_upper'))}\n"
        f"• Invalidation: < {format_price(watch.get('invalidation_price'))}\n"
        f"• Reason: {result.get('reason') or 'Struktur pullback gagal'}\n\n"
        "🔴 Entry reversal dibatalkan; tunggu setup baru."
    )



def run_live_reversal_engine(quotes: list[dict]) -> None:
    if not REVERSAL_LIVE_ENABLED:
        return

    retired_filter2 = cancel_filter2_reversal_watches()
    if retired_filter2:
        print(
            f"[FILTER2-RETIRED] cancelled_legacy_reversal_watches="
            f"{retired_filter2}"
        )

    eod_seed = None
    if EOD_ENTRY_ENGINE_ENABLED:
        try:
            eod_seed = seed_eod_entry_watches(
                quotes,
                max_price=MAX_ALERT_PRICE,
                max_plans=EOD_ENTRY_LIVE_PLAN_LIMIT,
            )
            print(
                f"[EOD-ENTRY] trade_date={eod_seed.get('trade_date')} "
                f"available={eod_seed.get('available', 0)} "
                f"near_zone={eod_seed.get('near_zone', 0)} "
                f"seeded={eod_seed.get('seeded', 0)}"
            )
        except Exception as exc:
            print(
                f"[EOD-ENTRY-WARN] {type(exc).__name__}: {str(exc)[:180]}"
            )

    # Live alerts remain a fallback. Active EOD watches are protected from being
    # overwritten by same-day 1-2 tick live plans.
    bootstrap_reversal_watches_from_recent_alerts()
    watches = get_active_reversal_watches(
        max_age_minutes=REVERSAL_WATCH_MINUTES,
        limit=max(50, EOD_ENTRY_LIVE_PLAN_LIMIT + 20),
    )
    if not watches:
        print("[REVERSAL-LIVE] no active EOD/live watch")
        return

    price_by_ticker = {
        str(item.get("ticker") or "").upper(): item.get("last")
        for item in quotes
        if item.get("ticker")
    }
    executable = prioritize_reversal_watches(
        watches,
        price_by_ticker,
        limit=REVERSAL_MAX_WATCHES,
    )

    print(
        f"[REVERSAL-LIVE] active={len(watches)} "
        f"selected={len(executable)} max_micro={REVERSAL_MAX_WATCHES}"
    )
    if not executable:
        return

    results: list[tuple[dict, dict]] = []
    worker_count = min(3, len(executable))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                evaluate_live_reversal_watch,
                watch,
                current_price=float(
                    price_by_ticker[str(watch["ticker"]).upper()]
                ),
            ): watch
            for watch in executable
        }
        for future in as_completed(future_map):
            watch = future_map[future]
            ticker = str(watch["ticker"]).upper()
            try:
                result = future.result()
            except Exception as exc:
                print(
                    f"[REVERSAL-LIVE-FAILED] {ticker}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            results.append((watch, result))

    for watch, result in results:
        ticker = str(watch["ticker"]).upper()
        status = result.get("status")
        print(
            f"[REVERSAL-LIVE] {ticker}: status={status} "
            f"price={result.get('current_price')} "
            f"score={result.get('score')} "
            f"delta={result.get('cumulative_delta')} "
            f"batch_delta={result.get('batch_delta')}"
        )

        if status == "ENTRY_CONFIRMED":
            signal_event_id = _OBS.record_signal(
                signal_type="REVERSAL_ENTRY_CONFIRMED",
                entity=ticker,
                score=result.get("score"),
                status="entry_confirmed",
                payload={
                    "source_lane": result.get("source_lane"),
                    "alert_price": result.get("alert_price"),
                    "current_price": result.get("current_price"),
                    "pullback_lower": result.get("pullback_lower"),
                    "pullback_upper": result.get("pullback_upper"),
                    "low_seen": result.get("low_seen"),
                    "cumulative_delta": result.get("cumulative_delta"),
                    "cumulative_buy_ratio": result.get("cumulative_buy_ratio"),
                    "batch_delta": result.get("batch_delta"),
                    "delta_flip": result.get("delta_flip"),
                    "selling_absorption_live": result.get("selling_absorption_live"),
                    "bid_percent": result.get("bid_percent"),
                    "matched_trade_count": result.get("cumulative_trade_count"),
                },
            )
            target = str(result.get("target") or watch.get("target") or "all")
            delivery = send_telegram(
                format_live_reversal_confirmed(result),
                target=target,
            )
            ok = bool(delivery.get("ok"))
            _OBS.record_delivery(
                channel="telegram",
                status="success" if ok else "failed",
                signal_event_id=signal_event_id,
                destination="configured-telegram-targets",
                metadata={
                    "ticker": ticker,
                    "signal_type": "REVERSAL_ENTRY_CONFIRMED",
                    "sent": delivery.get("sent", 0),
                    "failed": delivery.get("failed", 0),
                },
            )
            if ok:
                update_reversal_watch(
                    ticker,
                    state="CONFIRMED",
                    confirmed_ts=now_wib_iso(),
                )
                print(
                    f"[REVERSAL-ENTRY-ALERT] {ticker}: "
                    f"score={result.get('score')} sent={delivery.get('sent')}"
                )
            else:
                print(
                    f"[REVERSAL-ENTRY-TELEGRAM-FAILED] {ticker}; "
                    "watch remains active for retry."
                )

        elif status == "CANCELLED" and result.get("send_failed_alert"):
            target = str(watch.get("target") or "all")
            delivery = send_telegram(
                format_live_reversal_cancelled(result, watch),
                target=target,
            )
            _OBS.record_delivery(
                channel="telegram",
                status="success" if delivery.get("ok") else "failed",
                destination="configured-telegram-targets",
                metadata={
                    "ticker": ticker,
                    "signal_type": "REVERSAL_SETUP_CANCELLED",
                    "sent": delivery.get("sent", 0),
                    "failed": delivery.get("failed", 0),
                },
            )


def run_once() -> None:
    init_db()

    if not FORCE_RUN and not is_idx_market_window():
        print("Outside IDX regular market window. Scanner skipped.")
        return

    static_tickers = load_universe()

    shared_market_payload = None
    try:
        shared_market_payload = collect_shared_market_snapshot(length=5000)
        print(
            "[MARKET-COLLECTOR] IDX stock-summary ready "
            f"via {market_cache_backend()} namespace=shared-v1"
        )
    except Exception as exc:
        print(f"[WARN] market collector stock-summary gagal: {exc}")

    try:
        active_tickers = discover_active_tickers(
            TOP_MOVER_COUNT,
            payload=shared_market_payload,
        )
        if not active_tickers:
            print("[WARN] dynamic discovery returned 0 ticker")
    except Exception as exc:
        print(f"[WARN] dynamic discovery gagal: {exc}")
        active_tickers = []

    eod_plan_tickers: list[str] = []
    eod_plan_by_ticker: dict[str, dict] = {}
    if EOD_ENTRY_ENGINE_ENABLED:
        try:
            eod_snapshot = load_eod_entry_plans()
            eod_plan_by_ticker = {
                str(plan.get("ticker") or "").upper(): plan
                for plan in (eod_snapshot.get("plans") or [])
                if isinstance(plan, dict) and plan.get("ticker")
            }
            eod_plan_tickers = eod_plan_universe_tickers(
                eod_snapshot,
                max_price=MAX_ALERT_PRICE,
                limit=EOD_ENTRY_LIVE_PLAN_LIMIT,
            )
            print(
                f"[EOD-ENTRY-UNIVERSE] trade_date={eod_snapshot.get('trade_date')} "
                f"plans={len(eod_plan_tickers)}"
            )
        except Exception as exc:
            print(
                f"[EOD-ENTRY-UNIVERSE-WARN] "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )

    tickers = list(
        dict.fromkeys(
            static_tickers + active_tickers + eod_plan_tickers
        )
    )

    print(
        f"Universe static={len(static_tickers)} "
        f"active={len(active_tickers)} "
        f"eod={len(eod_plan_tickers)} "
        f"total={len(tickers)}"
    )
    print(f"Scanning {len(tickers)} ticker...")
    quotes = collect_quotes(tickers)
    print(f"Quote valid: {len(quotes)}")

    if not quotes:
        raise RuntimeError("Tidak ada quote valid. Periksa konfigurasi ZPI/API.")

    # Report from current stock-summary, or current quotes if IDX summary is stale.
    # No extra ZAPI request or workflow; Neon guards daily Telegram dedup.
    if TOP_GAINER_ENABLED and shared_market_payload is not None:
        try:
            top_gainer_status = send_daily_top_gainers(
                shared_market_payload,
                when=datetime.now().astimezone(),
                sender=send_telegram,
                fallback_quotes=quotes,
                store=NeonDailyDelivery(),
                count=TOP_GAINER_COUNT,
                min_coverage=TOP_GAINER_MIN_COVERAGE,
                after_hour=TOP_GAINER_SEND_AFTER_HOUR,
                target=TOP_GAINER_TARGET,
            )
            print(f"[TOP-GAINER] {top_gainer_status}")
        except Exception as exc:
            print(f"[TOP-GAINER-WARN] {type(exc).__name__}: {str(exc)[:160]}")

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

        eod_plan = eod_plan_by_ticker.get(ticker)
        if isinstance(eod_plan, dict):
            result["eod_entry_plan"] = eod_plan

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

    # Separate private-only potential watch: use existing analyzed scanner data,
    # never request any additional ZAPI endpoint or live orderbook call.
    if TOP_GAINER_POTENTIAL_ENABLED:
        try:
            potential_status = send_daily_potential(
                quotes,
                analyzed,
                summary_payload=shared_market_payload,
                when=datetime.now().astimezone(),
                sender=send_telegram,
                store=NeonDailyDelivery(namespace=POTENTIAL_NAMESPACE),
                snapshot_loader=lambda code: get_recent_snapshots(code, RECENT_SNAPSHOTS),
                count=TOP_GAINER_POTENTIAL_COUNT,
                after_hour=TOP_GAINER_POTENTIAL_SEND_AFTER_HOUR,
            )
            print(f"[TOP-GAINER-POTENTIAL] {potential_status}")
        except Exception as exc:
            print(f"[TOP-GAINER-POTENTIAL-WARN] {type(exc).__name__}: {str(exc)[:160]}")

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

    # Filter 2 is the same momentum family as Filter 1 with relaxed thresholds.
    # It is ranked from the same intraday features and still rejects falling knives.
    relaxed_pool = sorted(
        [
            x for x in analyzed
            if FILTER2_ENABLED and is_filter2_pre_candidate(x)
        ],
        key=lambda x: (
            float(x.get("score") or 0),
            float(x.get("volume_spike") or 0),
            float(x.get("activity_spike") or 0),
        ),
        reverse=True,
    )

    potential_tickers = {
        x["ticker"]
        for x in (locked_pool + ignition_pool + normal_pool + relaxed_pool)
    }
    for ticker in potential_tickers:
        evaluation_status[ticker] = "PENDING_MICRO"

    # Cascading scanner: Screener 1 is always evaluated first. Screener 2
    # receives no live microstructure call unless Screener 1 finishes with
    # zero final valid candidates. Four primary slots + at most two fallback
    # slots keep the original hard cap of six live microstructure candidates.
    primary_selected: list[dict] = []
    primary_selected_tickers: set[str] = set()

    filter2_reserve = (
        min(max(FILTER2_RESERVED_SLOTS, 0), MICRO_MAX_CANDIDATES)
        if FILTER2_ENABLED
        else 0
    )
    primary_capacity = max(0, MICRO_MAX_CANDIDATES - filter2_reserve)

    ignition_reserve = min(
        max(IGNITION_RESERVED_SLOTS, 0),
        primary_capacity,
    )
    primary_general_capacity = max(0, primary_capacity - ignition_reserve)

    add_eligible_from_pool(
        primary_selected,
        primary_selected_tickers,
        locked_pool,
        primary_general_capacity,
        screener_tag="LEGACY",
    )

    before_ignition = len(primary_selected)
    add_eligible_from_pool(
        primary_selected,
        primary_selected_tickers,
        ignition_pool,
        ignition_reserve,
        screener_tag="LEGACY",
    )

    for result in primary_selected[before_ignition:]:
        print(
            f"[IGNITION-PRE] {result['ticker']}: "
            f"vol={format_num(result.get('volume_spike'), 'x')} "
            f"interval={format_num(result.get('interval_return_pct'), '%')} "
            f"market_cap={format_market_cap(result.get('market_cap'))}"
        )

    # Reclaim unused primary slots for Screener 1 only. Filter 2 remains fully
    # dormant until Screener 1 has been micro-confirmed and finalized.
    remaining_primary = max(0, primary_capacity - len(primary_selected))
    if remaining_primary:
        add_eligible_from_pool(
            primary_selected,
            primary_selected_tickers,
            locked_pool,
            remaining_primary,
            screener_tag="LEGACY",
        )

    remaining_primary = max(0, primary_capacity - len(primary_selected))
    if remaining_primary:
        add_eligible_from_pool(
            primary_selected,
            primary_selected_tickers,
            normal_pool,
            remaining_primary,
            screener_tag="LEGACY",
        )

    print(
        f"Screener 1 microstructure candidates: {len(primary_selected)} "
        f"(primary_capacity={primary_capacity}, fallback_reserve={filter2_reserve}, "
        f"max_total={MICRO_MAX_CANDIDATES})"
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

    candidates: list[dict] = []

    # Stage 1 — Screener 1 only.
    primary_micro_results: dict[str, dict] = {}
    if primary_selected:
        worker_count = min(3, len(primary_selected))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_by_ticker = {
                executor.submit(fetch_microstructure, item["ticker"]): item["ticker"]
                for item in primary_selected
            }
            for future in as_completed(future_by_ticker):
                ticker = future_by_ticker[future]
                try:
                    primary_micro_results[ticker] = future.result()
                except Exception as exc:
                    evaluation_status[ticker] = "API_FAILED"
                    print(
                        f"[MICRO-FAILED] {ticker}: "
                        f"{type(exc).__name__}: {exc}"
                    )

    processed_primary: list[dict] = []
    primary_processed_by_ticker: dict[str, dict] = {}

    for result in primary_selected:
        ticker = result["ticker"]
        micro = primary_micro_results.get(ticker)
        if micro is None:
            continue

        micro = enrich_micro_with_tradeflow(
            ticker,
            micro,
            result.get("price"),
        )
        primary_micro_results[ticker] = micro
        result = select_next_primary_phase(
            apply_micro_confirmation(result, micro)
        )
        result = enrich_result_with_quality(result)
        processed_primary.append(result)
        primary_processed_by_ticker[ticker] = result

        print(
            f"[MICRO-F1] {ticker}: "
            f"micro={result.get('micro_score')} "
            f"buy={format_num((result.get('aggressive_buy_ratio') or 0) * 100, '%')} "
            f"bid={format_num(result.get('bid_percent'), '%')} "
            f"phase={result['phase']} "
            f"score={result['score']} "
            f"quality={result.get('quality_score')}"
        )

        if result["phase"] == "NONE":
            evaluation_status[ticker] = (
                "PENDING_PHASE" if result.get("pending_stage") else "NO_SIGNAL"
            )
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
                "transaction_value_ma50": result.get(
                    "transaction_value_ma50"
                ),
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

    # Stage 2 — relaxed clone of Filter 1. It runs only when Filter 1 has no
    # final valid ticker. Every next cycle starts from Filter 1 again.
    processed_filter2_fallback: list[dict] = []
    # Always initialize this cache because the private PTSN-style watcher below
    # can read it even when Filter 2 is skipped after Filter 1 finds a signal.
    fallback_micro_results: dict[str, dict] = {}

    if should_run_filter2_fallback(len(eligible_candidates)):
        print("[FILTER2-FALLBACK] Filter 1 returned 0 valid candidates; starting relaxed momentum.")
        filter2_stage: list[dict] = []
        filter2_new_selected: list[dict] = []
        filter2_tickers: set[str] = set()

        for result in relaxed_pool:
            if len(filter2_stage) >= filter2_reserve:
                break

            ticker = result["ticker"]
            if ticker in filter2_tickers:
                continue
            if not price_is_eligible(result.get("price")):
                continue
            if not prepare_market_cap_candidate(result):
                continue
            relaxed_daily_eligible = prepare_relaxed_daily_filter_candidate(result)
            record_dynamic_liquidity_observation(
                result,
                lane="FILTER2_RELAXED",
            )
            if not relaxed_daily_eligible:
                continue
            if not prepare_shareholder_candidate(result):
                continue
            if not relaxed_anti_falling_knife_pass(result):
                print(f"[FILTER2-KNIFE-SKIP] {ticker}")
                continue

            filter2_stage.append(result)
            filter2_tickers.add(ticker)
            if ticker not in primary_micro_results:
                filter2_new_selected.append(result)

            print(
                f"[FILTER2-PRE] {ticker}: "
                f"score={format_num(result.get('score'), '/100')} "
                f"vol={format_num(result.get('volume_spike'), 'x')} "
                f"activity={format_num(result.get('activity_spike'), 'x')} "
                f"interval={format_num(result.get('interval_return_pct'), '%')}"
            )

        if filter2_new_selected:
            worker_count = min(2, len(filter2_new_selected))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_by_ticker = {
                    executor.submit(fetch_microstructure, item["ticker"]): item["ticker"]
                    for item in filter2_new_selected
                }
                for future in as_completed(future_by_ticker):
                    ticker = future_by_ticker[future]
                    try:
                        fallback_micro_results[ticker] = future.result()
                    except Exception as exc:
                        evaluation_status[ticker] = "API_FAILED"
                        print(
                            f"[FILTER2-MICRO-FAILED] {ticker}: "
                            f"{type(exc).__name__}: {exc}"
                        )

        for raw_result in filter2_stage:
            ticker = raw_result["ticker"]
            micro = primary_micro_results.get(ticker) or fallback_micro_results.get(ticker)
            if micro is None:
                continue

            if ticker in fallback_micro_results:
                micro = enrich_micro_with_tradeflow(
                    ticker,
                    micro,
                    raw_result.get("price"),
                )
                fallback_micro_results[ticker] = micro

            result = apply_relaxed_micro_confirmation(raw_result, micro)
            result = enrich_result_with_quality(result)
            if result["phase"] == "NONE":
                evaluation_status[ticker] = "NO_SIGNAL"
            else:
                evaluation_status[ticker] = "FILTER2_SIGNAL"
                processed_filter2_fallback.append(result)

            print(
                f"[MICRO-F2] {ticker}: "
                f"phase={result.get('phase')} "
                f"score={result.get('score')} "
                f"micro={result.get('micro_score')} "
                f"quality={result.get('quality_score')} "
                f"anti_knife={result.get('anti_falling_knife_relaxed_pass')}"
            )
    elif FILTER2_ENABLED:
        print(
            f"[FILTER2-FALLBACK-SKIP] Filter 1 has "
            f"{len(eligible_candidates)} valid candidate(s); Filter 2 not run."
        )

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

    primary_alerted_tickers: set[str] = set()

    # Lifecycle first, ranking second: same-phase repeats and downgrades never
    # compete for Top-N. Only genuinely new setups or phase upgrades are ranked.
    primary_alert_pool: list[dict] = []
    for result in eligible_candidates:
        ticker = result["ticker"]
        if should_alert(result):
            primary_alert_pool.append(result)
        else:
            mark_signal_present(ticker)
            print(
                f"[NO-REPEAT] {ticker} "
                f"{result['phase']} {result['score']}"
            )

    primary_ranked, primary_suppressed = rank_top_n_candidates(
        primary_alert_pool,
        FILTER1_TOP_N_ALERTS_PER_CYCLE,
    )

    for result in primary_suppressed:
        ticker = result["ticker"]
        evaluation_status[ticker] = "TOP_N_SUPPRESSED"
        mark_signal_present(ticker)
        _OBS.record_signal(
            signal_type=result["phase"],
            entity=ticker,
            score=result.get("score"),
            status="top_n_suppressed",
            payload={
                "quality_score": result.get("quality_score"),
                "top_n_limit": FILTER1_TOP_N_ALERTS_PER_CYCLE,
                "transaction_value_ma50": result.get(
                    "transaction_value_ma50"
                ),
                "selling_absorption": result.get("selling_absorption"),
                "aggression_trend": result.get("aggression_trend"),
            },
        )
        print(
            f"[TOP-N-SUPPRESSED] {ticker}: "
            f"phase={result.get('phase')} "
            f"quality={result.get('quality_score')} "
            f"limit={FILTER1_TOP_N_ALERTS_PER_CYCLE}"
        )

    print(
        f"[TOP-N] Filter 1 alertable={len(primary_alert_pool)} "
        f"selected={len(primary_ranked)} "
        f"limit={FILTER1_TOP_N_ALERTS_PER_CYCLE}"
    )

    for rank, result in enumerate(primary_ranked, start=1):
        ticker = result["ticker"]
        signal_event_id = _OBS.record_signal(
            signal_type=result["phase"],
            entity=ticker,
            score=result.get("score"),
            status="alert_candidate",
            payload={
                "price": result.get("price"),
                "rank": rank,
                "quality_score": result.get("quality_score"),
                "quality_momentum_score": result.get(
                    "quality_momentum_score"
                ),
                "quality_micro_score": result.get("quality_micro_score"),
                "quality_tradeflow_score": result.get(
                    "quality_tradeflow_score"
                ),
                "quality_liquidity_score": result.get(
                    "quality_liquidity_score"
                ),
                "quality_foreign_score": result.get(
                    "quality_foreign_score"
                ),
                "quality_structure_score": result.get(
                    "quality_structure_score"
                ),
                "quality_risk_penalty": result.get(
                    "quality_risk_penalty"
                ),
                "foreign_score": result.get("foreign_score"),
                "foreign_status": result.get("foreign_status"),
                "micro_score": result.get("micro_score"),
                "selected_by": result.get("_selected_by"),
                "daily_change_pct": result.get("daily_change_pct"),
                "interval_return_pct": result.get("interval_return_pct"),
                "shareholder_count": result.get("shareholder_count"),
                "transaction_value_ma50": result.get(
                    "transaction_value_ma50"
                ),
                "aggressive_buy_value": result.get(
                    "aggressive_buy_value"
                ),
                "aggressive_sell_value": result.get(
                    "aggressive_sell_value"
                ),
                "net_aggression_value": result.get(
                    "net_aggression_value"
                ),
                "aggression_trend": result.get("aggression_trend"),
                "aggression_trend_samples": result.get(
                    "aggression_trend_samples"
                ),
                "aggression_trend_change_pct": result.get(
                    "aggression_trend_change_pct"
                ),
                "selling_absorption": result.get(
                    "selling_absorption"
                ),
            },
        )
        delivery = send_telegram(
            format_alert(result, rank=rank),
            target="group",
        )
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
                "rank": rank,
                "quality_score": result.get("quality_score"),
            },
        )

        if delivery_ok:
            mark_alerted(result)
            primary_alerted_tickers.add(ticker)
            register_reversal_watch_from_result(
                result,
                lane="FILTER1_STRICT",
                target="group",
            )
            print(
                f"[ALERT] #{rank} {ticker} "
                f"{result['phase']} score={result['score']} "
                f"quality={result.get('quality_score')}"
            )
        else:
            print(
                f"[WARN] Telegram gagal; state alert {ticker} "
                "tidak dinaikkan."
            )

    # Filter 2 is production-disabled. Keep this lane hard-gated so legacy
    # data or future refactors cannot send Filter 2 to either Telegram target.
    filter2_candidates = (
        sorted(
            processed_filter2_fallback,
            key=_quality_sort_key,
            reverse=True,
        )[:FILTER2_RESERVED_SLOTS]
        if FILTER2_ENABLED
        else []
    )

    for rank, result in enumerate(filter2_candidates, start=1):
        ticker = result["ticker"]
        if recent_screener_alert_exists(
            "FILTER2_RELAXED_MOMENTUM",
            ticker,
            FILTER2_ALERT_COOLDOWN_MINUTES,
        ):
            print(f"[FILTER2-NO-REPEAT] {ticker} {result.get('phase')} {result.get('score')}")
            continue

        signal_event_id = _OBS.record_signal(
            signal_type=f"FILTER2_{result.get('phase')}",
            entity=ticker,
            score=result.get("score"),
            status="alert_candidate",
            payload={
                "price": result.get("price"),
                "micro_score": result.get("micro_score"),
                "daily_change_pct": result.get("daily_change_pct"),
                "interval_return_pct": result.get("interval_return_pct"),
                "volume_spike": result.get("volume_spike"),
                "activity_spike": result.get("activity_spike"),
                "anti_falling_knife_pass": result.get("anti_falling_knife_relaxed_pass"),
                "volume_ma_ratio": result.get("volume_ma_ratio"),
                "shareholder_count": result.get("shareholder_count"),
            },
        )
        delivery = send_telegram(format_filter2_alert(result, rank), target="private" if ticker == "PTSN" else "all")
        delivery_ok = bool(delivery.get("ok"))
        _OBS.record_delivery(
            channel="telegram",
            status="success" if delivery_ok else "failed",
            signal_event_id=signal_event_id,
            destination="configured-telegram-targets",
            metadata={
                "sent": delivery.get("sent", 0),
                "failed": delivery.get("failed", 0),
                "signal_type": f"FILTER2_{result.get('phase')}",
                "ticker": ticker,
            },
        )

        if delivery_ok:
            save_screener_alert(
                "FILTER2_RELAXED_MOMENTUM",
                ticker,
                float(result.get("score") or 0),
                result.get("price"),
            )
            register_reversal_watch_from_result(
                result,
                lane="FILTER2_RELAXED",
                target="private" if ticker == "PTSN" else "all",
            )
            print(
                f"[FILTER2-ALERT] #{rank} {ticker} "
                f"{result.get('phase')} {result.get('score')}"
            )
        else:
            print(f"[WARN] Filter 2 Telegram gagal untuk {ticker}; cooldown tidak disimpan.")


    # Private-only PTSN-style structure watch, from snapshots already collected.
    # It does not add another scanner workflow or request additional microstructure.
    if PTSN_STRUCTURE_ENABLED:
        private_candidates: list[dict] = []
        for features in analyzed:
            ticker = features["ticker"]
            if not price_is_eligible(features.get("price")):
                continue
            if not features.get("anti_falling_knife_pass") or not features.get("not_overextended"):
                continue

            snapshots = get_recent_snapshots(ticker, RECENT_SNAPSHOTS)
            micro = primary_micro_results.get(ticker) or fallback_micro_results.get(ticker)
            candidate = build_ptsn_structure(snapshots, features, micro)
            if candidate is None:
                continue

            # Cache-only guards: NO additional ZAPI calls for private momentum.
            cap = features.get("market_cap")
            if cap is None:
                cap = get_cached_market_cap(ticker, max_age_hours=MARKET_CAP_CACHE_HOURS)
            if MARKET_CAP_FILTER_ENABLED and not market_cap_is_eligible(cap):
                continue

            metrics = get_cached_daily_filter(ticker, max_age_hours=DAILY_FILTER_CACHE_HOURS)
            if metrics:
                observation_result = {
                    **features,
                    **metrics,
                    "ticker": ticker,
                    "market_cap": cap,
                }
                record_dynamic_liquidity_observation(
                    observation_result,
                    lane="PTSN_STRUCTURE",
                )
            if DAILY_FILTER_ENABLED and (
                not metrics or not metrics.get("enough_history")
                or metrics.get("value_ma") is None
                or float(metrics["value_ma"]) <= MIN_VALUE_MA_IDR
            ):
                continue

            if SHAREHOLDER_FILTER_ENABLED:
                # Do not call the shareholder endpoint solely for this private watch.
                if not prepare_shareholder_candidate_from_cache(ticker):
                    continue

            private_candidates.append(candidate)

        private_candidates.sort(key=lambda x: float(x["structure_score"]), reverse=True)
        delivered = 0
        for candidate in private_candidates:
            if delivered >= PTSN_STRUCTURE_MAX_ALERTS_PER_CYCLE:
                break
            ticker = candidate["ticker"]
            stage = candidate["stage"]
            cooldown_key = f"PTSN_STRUCTURE_{stage}"
            if recent_screener_alert_exists("PTSN_STRUCTURE_CONFIRMED", ticker, 1440):
                continue
            if recent_screener_alert_exists(cooldown_key, ticker, PTSN_STRUCTURE_COOLDOWN_MINUTES):
                continue
            delivery = send_telegram(format_ptsn_structure(candidate), target="private")
            if delivery.get("ok"):
                save_screener_alert(cooldown_key, ticker, float(candidate["structure_score"]), candidate["price"])
                delivered += 1
                print(f"[PTSN-PRIVATE] {ticker} {stage} score={candidate['structure_score']}")
            else:
                print(f"[PTSN-PRIVATE-FAILED] {ticker}: private alert not recorded")

    # Post-alert lifecycle: only recently alerted tickers are watched, and live
    # running-trade/orderbook calls start after a pullback/retest is observed.
    run_live_reversal_engine(quotes)


if __name__ == "__main__":
    run_once()
