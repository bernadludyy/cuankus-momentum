from __future__ import annotations

from typing import Any, Iterable
import re
import time

import requests

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


def _headers() -> dict[str, str]:
    validate_zpi_config()
    return {
        "x-api-key": ZPI_API_KEY,
        "accept": "application/json",
    }


def _request_json(
    url: str,
    params: dict | None = None,
    timeout: int = 30,
    attempts: int = 3,
) -> Any:
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                headers=_headers(),
                params=params or {},
                timeout=timeout,
            )

            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()

            response.raise_for_status()
            return response.json()

        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
        ) as exc:
            last_exc = exc

            status = getattr(getattr(exc, "response", None), "status_code", None)
            retryable_http = status is None or status == 429 or status >= 500

            if attempt >= attempts or not retryable_http:
                raise

            wait_seconds = attempt
            print(
                f"[RETRY] API attempt {attempt}/{attempts} gagal "
                f"({type(exc).__name__}); retry {wait_seconds}s..."
            )
            time.sleep(wait_seconds)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("API request gagal tanpa exception detail")


def _get(path: str, params: dict | None = None, timeout: int = 30) -> Any:
    validate_zpi_config()

    path = path.strip().lstrip("/")
    url = f"{ZPI_BASE_URL}/{path}"

    return _request_json(
        url,
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
    url = f"{base_url.rstrip('/')}/{path.strip().lstrip('/')}"
    return _request_json(
        url,
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


def get_stock_summary_row(ticker: str) -> dict[str, Any]:
    row = _LAST_STOCK_SUMMARY_BY_TICKER.get(ticker.upper().strip())
    return dict(row) if row else {}


def discover_active_tickers(count: int | None = None) -> list[str]:
    global _LAST_STOCK_SUMMARY_BY_TICKER

    """Temukan saham aktif dari seluruh ringkasan IDX.

    Ambil seluruh stock-summary lalu gabungkan Top N berdasarkan:
    Frequency, Value, dan Volume. Ini lebih robust daripada bergantung
    pada respons top-movers saja.
    """
    top_n = count or TOP_MOVER_COUNT
    payload = _unwrap_project_payload(fetch_stock_summary(length=5000))

    if isinstance(payload, dict):
        rows = payload.get("data", [])
    elif isinstance(payload, list):
        rows = payload
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
    """Fetch daily IDX history used by Bot 4 liquidity filters."""
    return _get_from_base(
        ZPI_IDX_BASE_URL,
        "stock-history",
        {
            "code": ticker.upper().strip(),
            "length": int(length),
        },
    )


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

    return _get("running-trades", params)



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
