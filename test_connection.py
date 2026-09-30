import json

from scanner.market_data import fetch_quote
from scanner.normalizer import normalize_quote

ticker = "BBCA"
raw = fetch_quote(ticker)

print("=" * 70)
print("ZPI CONNECTION OK")
print("=" * 70)
print("RAW RESPONSE:")
print(json.dumps(raw, indent=2, ensure_ascii=False))

print("\n" + "=" * 70)
print("NORMALIZED:")
print("=" * 70)
print(
    json.dumps(
        normalize_quote(raw, ticker_hint=ticker),
        indent=2,
        ensure_ascii=False,
    )
)
