import json

from scanner.microstructure import fetch_microstructure

ticker = "BBCA"

result = fetch_microstructure(ticker)

print("=" * 70)
print(f"MICROSTRUCTURE TEST: {ticker}")
print("=" * 70)
print(json.dumps(result, indent=2, ensure_ascii=False))
