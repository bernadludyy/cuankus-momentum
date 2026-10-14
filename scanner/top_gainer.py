"""Quiet daily IDX Top Gainer digest from the EXISTING stock-summary payload.

No additional upstream requests, new cron jobs, stock-history calls or micro slots.
The shared Neon cache_entries table is used as a durable daily delivery guard.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import os
import re
import uuid
from typing import Any, Callable

from project_fabric.postgres_backend import configured_database_url
from project_fabric.universe_intelligence import _items

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    psycopg = None
    Jsonb = None

WIB = timezone(timedelta(hours=7))
_CODE = re.compile(r"^[A-Z0-9]{2,15}$")
NAMESPACE = "bot4-topgainer-daily-v1"


def _num(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "" or isinstance(value, bool):
            continue
        try:
            number = float(str(value).replace(",", ""))
            if math.isfinite(number):
                return number
        except (ValueError, TypeError):
            pass
    return None


def _day(row: dict) -> date | None:
    raw = row.get("Date") or row.get("date") or row.get("TradingDate")
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def rank_top_gainers(
    payload: Any,
    *,
    trade_date: date,
    count: int = 10,
    min_coverage: int = 300,
) -> tuple[list[dict], int]:
    """Rank all valid current-day IDX rows. A stale/partial feed fails closed.

    Close = last IDX stock-summary price; Previous = preceding session close.
    Ranking is purely day-over-day percentage gain, NOT the Bot 4 momentum
    score and NOT limited by Bot 4's Rp500 price filter.
    """
    rows = _items(payload)
    today_rows = [row for row in rows if _day(row) == trade_date]
    if len(today_rows) < min_coverage or len(today_rows) < 0.75 * len(rows):
        return [], 0

    all_valid: dict[str, dict] = {}
    for row in today_rows:
        ticker = str(row.get("StockCode") or row.get("Code") or row.get("code") or "").upper().strip()
        if not _CODE.fullmatch(ticker) or ticker in all_valid:
            continue
        last = _num(row, "Close", "close")
        previous = _num(row, "Previous", "previous", "PrevClose", "prev_close")
        if last is None or previous is None or last <= 0 or previous <= 0:
            continue
        turnover = _num(row, "Value", "value")
        volume = _num(row, "Volume", "volume")
        frequency = _num(row, "Frequency", "frequency")
        # A nontraded share or invalid source row must not enter a live leaderboard.
        if turnover is None or turnover <= 0 or volume is None or volume <= 0 or frequency is None or frequency <= 0:
            continue
        gain_pct = (last - previous) / previous * 100.0
        all_valid[ticker] = {
            "ticker": ticker,
            "last": last,
            "previous": previous,
            "change": last - previous,
            "gain_pct": round(gain_pct, 4),
            "value": turnover,
            "frequency": int(frequency),
        }

    coverage = len(all_valid)
    if coverage < min_coverage:
        return [], coverage
    ranked = sorted(
        (item for item in all_valid.values() if item["gain_pct"] > 0),
        key=lambda item: (-item["gain_pct"], -item["value"], item["ticker"]),
    )
    return ranked[:max(1, min(int(count), 30))], coverage



def rank_live_scan_gainers(
    quotes: list[dict],
    *,
    summary_payload: Any,
    trade_date: date,
    count: int = 10,
    min_coverage: int = 30,
) -> tuple[list[dict], int]:
    """Fallback: Top Gainers ONLY among tickers already quoted in this scan.

    The shared stock-summary can lag a session. Today's prices come from
    existing multi-quote calls; previous close is taken from quote.prev_close,
    falling back to yesterday's stock-summary Close if it is recent enough.
    No fresh API requests are permitted here.
    """
    historical_rows = [
        row for row in _items(summary_payload)
        if _day(row) is not None and _day(row) < trade_date
    ]
    latest_previous_day = max((_day(row) for row in historical_rows), default=None)
    summary_prev = {}
    if latest_previous_day and (trade_date - latest_previous_day).days <= 7:
        for row in historical_rows:
            if _day(row) != latest_previous_day:
                continue
            ticker = str(row.get("StockCode") or row.get("Code") or "").strip().upper()
            previous = _num(row, "Close", "close")
            if _CODE.fullmatch(ticker) and previous is not None and previous > 0:
                summary_prev[ticker] = previous

    unique = {}
    for quote in quotes:
        ticker = str(quote.get("ticker") or "").upper().strip()
        if not _CODE.fullmatch(ticker) or ticker in unique:
            continue
        last = _num(quote, "last")
        previous = _num(quote, "prev_close")
        if previous is None or previous <= 0:
            previous = summary_prev.get(ticker)
        if last is None or previous is None or last <= 0 or previous <= 0:
            continue
        gain_pct = (last - previous) / previous * 100.0
        unique[ticker] = {
            "ticker": ticker,
            "last": last,
            "previous": previous,
            "change": last - previous,
            "gain_pct": round(gain_pct, 4),
            "value": None,  # summary's stale Value must NEVER be shown as live
        }
    coverage = len(unique)
    if coverage < min_coverage:
        return [], coverage
    return sorted(
        (row for row in unique.values() if row["gain_pct"] > 0),
        key=lambda row: (-row["gain_pct"], row["ticker"]),
    )[:max(1, min(int(count), 30))], coverage

def _idr(value: float) -> str:
    number = float(value)
    if number >= 1e12:
        return f"Rp{number / 1e12:.2f}T"
    if number >= 1e9:
        return f"Rp{number / 1e9:.2f}M"
    if number >= 1e6:
        return f"Rp{number / 1e6:.1f}jt"
    return f"Rp{number:,.0f}".replace(",", ".")


def format_top_gainer_report(
    rows: list[dict], *, trade_date: date, when: datetime, coverage: int,
    scope: str = "idx",
) -> str:
    wib = when.astimezone(WIB)
    lines = [
        f"🏆 TOP GAINER {'IDX' if scope == 'idx' else 'UNIVERSE SCANNER'} | {trade_date.strftime('%d-%m-%Y')}",
        f"🕙 Snapshot scanner: {wib:%H:%M} WIB",
        f"📊 {'Ranking IDX' if scope == 'idx' else 'Hanya saham terpantau scanner'}: {coverage} data valid",
        "",
    ]
    for rank, item in enumerate(rows, 1):
        price = f"{item['last']:,.0f}".replace(",", ".")
        line = f"{rank:02d}. {item['ticker']} | Rp{price} | +{item['gain_pct']:.2f}%"
        if item.get("value") is not None:
            line += f"\n     Nilai transaksi: {_idr(item['value'])}"
        lines.append(line)
    lines.extend([
        "",
        ("Sumber: ZAPI IDX stock-summary (Close vs Previous)."
         if scope == "idx" else
         "Sumber: quote live scanner + previous close EOD. BUKAN ranking seluruh BEI."),
        "Ranking harga saja, BUKAN rekomendasi entry.",
        "🔕 Otomatis 1 laporan/hari; Filter 1 tetap berjalan terpisah.",
    ])
    return "\n".join(lines)


class NeonDailyDelivery:
    """Atomic day-based Telegram send claim; no local fallback that could spam."""

    def __init__(self, database_url: str | None = None, owner: str | None = None, namespace: str = NAMESPACE):
        self.database_url = database_url if database_url is not None else configured_database_url()
        self.owner = owner or f"{os.getenv('GITHUB_RUN_ID', 'local')}-{uuid.uuid4().hex}"
        self.namespace = namespace

    def _connect(self):
        if not self.database_url or psycopg is None or Jsonb is None:
            raise RuntimeError("Neon delivery dedup unavailable: cannot safely send Top Gainer")
        return psycopg.connect(self.database_url, connect_timeout=6, autocommit=True)

    def claim(self, day: date) -> bool:
        """Only one concurrent sender may claim; retry abandoned claims after 10m."""
        with self._connect() as cn, cn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO project_fabric.cache_entries
                    (namespace, cache_key, value_json, source_service, saved_at)
                VALUES (%s, %s, %s, 'cuankus-momentum', now())
                ON CONFLICT (namespace, cache_key) DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    saved_at = now()
                WHERE project_fabric.cache_entries.value_json->>'status' <> 'sent'
                  AND project_fabric.cache_entries.saved_at < now() - interval '10 minutes'
                RETURNING cache_key
                """,
                (self.namespace, day.isoformat(), Jsonb({"status": "pending", "owner": self.owner})),
            )
            return cur.fetchone() is not None

    def complete(self, day: date, *, success: bool) -> None:
        with self._connect() as cn, cn.cursor() as cur:
            if success:
                cur.execute(
                    """
                    UPDATE project_fabric.cache_entries SET
                        value_json=%s, saved_at=now()
                    WHERE namespace=%s AND cache_key=%s
                      AND value_json->>'owner'=%s
                    """,
                    (Jsonb({"status": "sent", "owner": self.owner}), self.namespace, day.isoformat(), self.owner),
                )
            else:
                cur.execute(
                    """
                    DELETE FROM project_fabric.cache_entries
                    WHERE namespace=%s AND cache_key=%s AND value_json->>'owner'=%s
                    """,
                    (self.namespace, day.isoformat(), self.owner),
                )


def send_daily_top_gainers(
    payload: Any,
    *,
    when: datetime,
    sender: Callable[..., dict],
    fallback_quotes: list[dict] | None = None,
    store: Any,
    count: int = 10,
    min_coverage: int = 300,
    after_hour: int = 10,
    target: str = "private",
) -> str:
    """Send once after 10 WIB, without a new API call or Actions job.

    No historical data is presented as live. Delivery is marked only on success;
    if Neon is unavailable, the caller logs the failure and does not send.
    """
    if when.tzinfo is None:
        raise ValueError("when must be timezone-aware")
    local = when.astimezone(WIB)
    if local.hour < after_hour or local.hour >= 16:
        return "outside_delivery_window"
    rows, coverage = rank_top_gainers(
        payload, trade_date=local.date(), count=count, min_coverage=min_coverage,
    )
    scope = "idx"
    if not rows and fallback_quotes:
        rows, coverage = rank_live_scan_gainers(
            fallback_quotes,
            summary_payload=payload,
            trade_date=local.date(),
            count=count,
        )
        scope = "scanner"
    if not rows:
        return f"invalid_or_stale_summary_and_scan:coverage={coverage}"
    if not store.claim(local.date()):
        return "already_sent_or_claimed"

    try:
        message = format_top_gainer_report(rows, trade_date=local.date(), when=local, coverage=coverage, scope=scope)
        # Hard-enforced privacy: never deliver this report to Cuankus group.
        result = sender(message, target="private")
        delivered = bool(result.get("ok"))
    except Exception:
        store.complete(local.date(), success=False)
        raise
    store.complete(local.date(), success=delivered)
    return f"sent:{len(rows)}:scope={scope}" if delivered else "telegram_failed_retryable"
