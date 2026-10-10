import os

from scanner.daily_filters import (
    compute_daily_filter_metrics,
    daily_filter_is_eligible,
)
from scanner.market_data import fetch_stock_history


ticker = os.getenv("TEST_TICKER", "BKSL").upper().strip()
raw = fetch_stock_history(ticker, length=120)
metrics = compute_daily_filter_metrics(raw)

print(f"Ticker: {ticker}")
print(f"History days: {metrics.get('history_days')}")
print(f"Volume MA10: {metrics.get('volume_ma_short')}")
print(f"Volume MA100: {metrics.get('volume_ma_long')}")
print(f"Volume MA10/MA100: {metrics.get('volume_ma_ratio')}")
print(f"Value MA5: {metrics.get('value_ma')}")
print(f"Transaction Value MA50: {metrics.get('transaction_value_ma50')}")
print(f"Eligible Filter 1 V2: {daily_filter_is_eligible(metrics)}")

if int(metrics.get("history_days") or 0) <= 0:
    raise RuntimeError("IDX stock-history tidak mengembalikan data harian.")
