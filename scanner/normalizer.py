from __future__ import annotations

from typing import Any


ALIASES = {
    "ticker": (
        "code", "symbol", "ticker", "stock_code", "stockcode",
        "security_code", "securitycode",
    ),
    "last": (
        "last", "price", "close", "last_price", "lastprice",
        "current", "current_price", "currentprice",
    ),
    "open": ("open", "open_price", "openprice"),
    "high": ("high", "high_price", "highprice"),
    "low": ("low", "low_price", "lowprice"),
    "prev_close": (
        "prev_close", "prevclose", "previous", "previous_close",
        "previousclose", "preclose", "yesterday", "prior_close",
    ),
    "volume": (
        "volume", "vol", "total_volume", "totalvolume",
        "trade_volume", "tradevolume",
    ),
    "value": (
        "value", "amount", "turnover", "total_value", "totalvalue",
        "transaction_value", "transactionvalue",
    ),
    "frequency": (
        "frequency", "freq", "trade_count", "tradecount",
        "trades", "total_frequency", "totalfrequency",
        "transaction_count", "transactioncount",
    ),
    "bid": ("bid", "best_bid", "bestbid", "bid_price", "bidprice"),
    "offer": (
        "offer", "ask", "best_offer", "bestoffer",
        "best_ask", "bestask", "offer_price", "offerprice",
    ),
}


def _clean_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "-", "nan"}:
        return None

    text = text.replace("Rp", "").replace("rp", "").strip()
    text = text.replace("%", "").strip()
    text = text.replace(",", "")

    try:
        return float(text)
    except ValueError:
        return None


def _flatten_scalars(obj: Any, out: dict[str, Any] | None = None) -> dict[str, Any]:
    if out is None:
        out = {}

    if isinstance(obj, dict):
        for key, value in obj.items():
            clean = _clean_key(key)
            if isinstance(value, (dict, list)):
                _flatten_scalars(value, out)
            else:
                out.setdefault(clean, value)

    elif isinstance(obj, list):
        for item in obj:
            _flatten_scalars(item, out)

    return out


def _pick(flat: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in flat:
            return flat[name]
    return None


def normalize_quote(payload: Any, ticker_hint: str | None = None) -> dict[str, Any]:
    flat = _flatten_scalars(payload)

    ticker = _pick(flat, ALIASES["ticker"])
    if ticker is None:
        ticker = ticker_hint

    result = {
        "ticker": str(ticker).upper().strip() if ticker else None,
    }

    for field in (
        "last", "open", "high", "low", "prev_close",
        "volume", "value", "frequency", "bid", "offer",
    ):
        result[field] = _to_number(_pick(flat, ALIASES[field]))

    return result


def _looks_like_quote_dict(obj: dict[str, Any]) -> bool:
    keys = {_clean_key(k) for k in obj.keys()}
    has_ticker = any(k in keys for k in ALIASES["ticker"])
    has_price = any(k in keys for k in ALIASES["last"])
    return has_ticker and has_price


def _find_quote_dicts(obj: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if _looks_like_quote_dict(obj):
            found.append(obj)
            return
        for value in obj.values():
            _find_quote_dicts(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_quote_dicts(item, found)


def extract_quotes(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _find_quote_dicts(payload, found)

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in found:
        quote = normalize_quote(item)
        ticker = quote.get("ticker")
        if ticker and ticker not in seen:
            seen.add(ticker)
            normalized.append(quote)

    return normalized
