from __future__ import annotations

from typing import Any, Iterable
import os
import re

from project_fabric.data_gateway import DataGateway, market_policy
from project_fabric.universe_intelligence import publish_summary, publish_history
from project_fabric.market_archive import MarketArchive

from config import (
    ZPI_API_KEY,
    ZPI_BASE_URL,
    ZPI_QUOTE_PATH,
    ZPI_SUMMARY_PATH,
    ZPI_IDX_BASE_URL,
    TOP_MOVER_COUNT,
    validate_zpi_config,
)


_LAST_STOCK_SUMMARY_BY_TICKER: dict[str, dict[str, Any]] = {}
_GATEWAY = DataGateway(service="cuankus-momentum")
_ARCHIVE = MarketArchive(service="cuankus-momentum")
_VOLUME_BY_PRICE_ENABLED = os.getenv(
    "VOLUME_BY_PRICE_ENABLED", "true"
).lower() in {"1", "true", "yes", "y"}


def _headers() -> dict[str, str]:
    validate_zpi_config()
    return {
        "x-api-key": ZPI_API_KEY,
        "accept": "application/json",
    }


def _request_json(
    url: str,
    *,
    endpoint: str,
    family: str,
    params: dict | None = None,
    timeout: int = 30,
) -> Any:
    return _GATEWAY.get_json(
        endpoint=f"{family}:{endpoint}",
        url=url,
        headers=_headers(),
        params=params or {},
        timeout=timeout,
        policy=market_policy(family, endpoint, params or {}),
    )


def _get(path: str, params: dict | None = None, timeout: int = 30) -> Any:
    validate_zpi_config()

    path = path.strip().lstrip("/")
    url = f"{ZPI_BASE_URL}/{path}"

    return _request_json(
        url,
        endpoint=path,
        family="pluang",
        params=params,
        timeout=timeout,
    )


def fetch_quote(ticker: str) -> Any:
    return _get(
        ZPI_QUOTE_PATH,
        {"code": ticker.upper().strip()},
    )


def fetch_multi_quote(tickers: Iterable[str]) -> Any:
    codes = ",".join(t.upper().strip() for t in tickers if t.strip())
    return _get(
        ZPI_SUMMARY_PATH,
        {"codes": codes},
    )


def audit_endpoint(path: str, params: dict | None = None) -> Any:
    return _get(path, params or {})


def _get_from_base(base_url: str, path: str, params: dict | None = None, timeout: int = 30) -> Any:
    validate_zpi_config()
    path = path.strip().lstrip("/")
    url = f"{base_url.rstrip('/')}/{path}"
    family = "idx" if base_url.rstrip("/") == ZPI_IDX_BASE_URL.rstrip("/") else "pluang"
    return _request_json(
        url,
        endpoint=path,
        family=family,
        params=params,
        timeout=timeout,
    )


def fetch_top_movers(mover_type: str, count: int | None = None) -> Any:
    allowed = {"gainer", "loser", "volume", "value", "frequent"}
    if mover_type not in allowed:
        raise ValueError(f"mover_type harus salah satu: {sorted(allowed)}")

    return _get_from_base(
        ZPI_IDX_BASE_URL,
        "top-movers",
        {
            "type": mover_type,
            "resultCount": count or TOP_MOVER_COUNT,
        },
    )


def _unwrap_project_payload(payload: Any) -> Any:
    """Zapi kadang membungkus response sebagai {"project": ..., "data": ...}."""
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


def fetch_stock_summary(length: int = 5000) -> Any:
    return _get_from_base(
        ZPI_IDX_BASE_URL,
        "stock-summary",
        {
            "length": length,
            "start": 0,
        },
    )


def _index_stock_summary(payload: Any) -> list[dict[str, Any]]:
    """Normalize one full IDX stock-summary payload and index it by ticker."""
    global _LAST_STOCK_SUMMARY_BY_TICKER

    normalized = _unwrap_project_payload(payload)
    if isinstance(normalized, dict):
        rows = normalized.get("data", [])
    elif isinstance(normalized, list):
        rows = normalized
    else:
        rows = []

    rows = [row for row in rows if isinstance(row, dict)]
    _LAST_STOCK_SUMMARY_BY_TICKER = {
        str(
            row.get("StockCode")
            or row.get("Code")
            or row.get("code")
            or ""
        ).upper().strip(): row
        for row in rows
        if (
            row.get("StockCode")
            or row.get("Code")
            or row.get("code")
        )
    }
    return rows


def collect_shared_market_snapshot(length: int = 5000) -> Any:
    """Fetch/publish the common IDX snapshot through Project Fabric.

    DataGateway stores this payload in the shared Neon cache when an upstream
    ZAPI request is required. Other repos using namespace shared-v1 and the
    same endpoint parameters can reuse it without another upstream request.
    """
    payload = fetch_stock_summary(length=length)
    _index_stock_summary(payload)
    try:
        enriched = publish_summary(_GATEWAY.cache, payload)
        print(f"[UNIVERSE-INTELLIGENCE] summary indexed={enriched['count']} source=stock-summary")
    except Exception as exc:
        # Non-critical enrichment must never interrupt existing market scans.
        print(f"[UNIVERSE-INTELLIGENCE-WARN] summary: {type(exc).__name__}: {str(exc)[:160]}")
    if _ARCHIVE.ready:
        try:
            count = _ARCHIVE.save_summary(payload)
            if count:
                print(f"[MARKET-ARCHIVE] completed stock-summary rows={count}")
        except Exception as exc:
            print(f"[MARKET-ARCHIVE-WARN] summary: {type(exc).__name__}")
    return payload


def market_cache_backend() -> str:
    return _GATEWAY.cache_backend


def get_stock_summary_row(ticker: str) -> dict[str, Any]:
    row = _LAST_STOCK_SUMMARY_BY_TICKER.get(ticker.upper().strip())
    return dict(row) if row else {}


def discover_active_tickers(
    count: int | None = None,
    payload: Any | None = None,
) -> list[str]:
    """Temukan saham aktif dari seluruh ringkasan IDX.

    Ambil seluruh stock-summary lalu gabungkan Top N berdasarkan Frequency,
    Value, dan Volume. Payload collector dapat dipakai langsung sehingga
    collector dan scanner membaca snapshot pasar yang sama.
    """
    top_n = count or TOP_MOVER_COUNT
    if payload is None:
        payload = fetch_stock_summary(length=5000)
    rows = _index_stock_summary(payload)

    if not rows:
        return []
    def number(row: dict, key: str) -> float:
        value = row.get(key)
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    selected: list[dict] = []
    for metric in ("Frequency", "Value", "Volume"):
        ranked = sorted(
            rows,
            key=lambda row: number(row, metric),
            reverse=True,
        )
        selected.extend(ranked[:top_n])

    tickers: list[str] = []
    for row in selected:
        code = (
            row.get("StockCode")
            or row.get("Code")
            or row.get("code")
        )
        if code:
            tickers.append(str(code).upper().strip())

    return list(dict.fromkeys(tickers))



def fetch_stock_history(ticker: str, length: int = 120) -> Any:
    """Reuse canonical 120-day history and publish derived features when available."""
    payload = _get_from_base(
        ZPI_IDX_BASE_URL,
        "stock-history",
        {
            "code": ticker.upper().strip(),
            "length": int(length),
        },
    )
    if int(length) >= 30:
        try:
            publish_history(_GATEWAY.cache, ticker, payload)
        except Exception as exc:
            print(f"[UNIVERSE-INTELLIGENCE-WARN] history {ticker}: {type(exc).__name__}: {str(exc)[:160]}")
        if _ARCHIVE.ready:
            try:
                stored = _ARCHIVE.save_history(ticker, payload)
                if stored:
                    _ARCHIVE.compute_score(ticker)
            except Exception as exc:
                print(f"[MARKET-ARCHIVE-WARN] history {ticker}: {type(exc).__name__}")
    return payload


def fetch_company_announcements(ticker: str, length: int = 50) -> Any:
    """Fetch IDX disclosures for one issuer via the existing ZAPI IDX proxy."""
    return _get_from_base(
        ZPI_IDX_BASE_URL,
        "company-announcements",
        {
            "code": ticker.upper().strip(),
            "length": int(length),
            "start": 0,
            "locale": "id",
        },
    )

def fetch_orderbook(ticker: str) -> Any:
    return _get(
        "orderbook",
        {"code": ticker.upper().strip()},
    )


def fetch_running_trades(
    ticker: str,
    min_lot: int = 100,
    action: str | None = None,
) -> Any:
    params: dict[str, Any] = {
        "code": ticker.upper().strip(),
        "minLot": min_lot,
    }
    if action:
        params["action"] = action.upper().strip()

    payload = _get("running-trades", params)
    if _VOLUME_BY_PRICE_ENABLED and _ARCHIVE.ready:
        try:
            saved = _ARCHIVE.save_volume_by_price(
                ticker,
                payload,
                minimum_lot=max(1, int(min_lot)),
            )
            if saved["new_trades"]:
                print(
                    f"[VOLUME-BY-PRICE] {ticker.upper().strip()}: "
                    f"new_trades={saved['new_trades']} "
                    f"levels={saved['updated_price_levels']} "
                    f"min_lot={max(1, int(min_lot))}"
                )
        except Exception as exc:
            # Observational storage must never block the live scanner.
            print(
                f"[VOLUME-BY-PRICE-WARN] {ticker.upper().strip()}: "
                f"{type(exc).__name__}: {str(exc)[:160]}"
            )
    return payload



def _find_key_recursive(payload: Any, target: str) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip().lower() == target.lower():
                return value
            found = _find_key_recursive(value, target)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_key_recursive(item, target)
            if found is not None:
                return found
    return None


def _parse_compact_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().upper()
    if not text or text in {"-", "NONE", "NULL", "N/A"}:
        return None

    text = text.replace("RP", "").replace(",", "").strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMBT]?)", text)
    if not match:
        return None

    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {
        "": 1.0,
        "K": 1e3,
        "M": 1e6,
        "B": 1e9,
        "T": 1e12,
    }[suffix]
    return number * multiplier


def extract_market_cap(payload: Any) -> float | None:
    """Extract Pluang keyStats.market_cap and normalize it to IDR."""
    for key in ("market_cap", "marketcap", "marketCap"):
        raw = _find_key_recursive(payload, key)
        parsed = _parse_compact_number(raw)
        if parsed is not None:
            return parsed
    return None
