from bot.notifier import send_telegram
from main import format_alert, format_flow_alert


sample_result = {
    "phase": "POWERFUL_MOMENTUM",
    "ticker": "FORMAT-TEST",
    "price": 79.0,
    "score": 83.8,
    "leakage_score": 27.2,
    "momentum_score": 83.8,
    "ignition_score": 90.0,
    "flow_pre_score": 82.0,
    "flow_score": 86.4,
    "flow_status": "STRONG FLOW CONFLUENCE",
    "flow_reasons": [
        "Foreign flow kuat 92.0/100",
        "Microstructure kuat 80.0/100",
        "Aggressive buy 75%",
        "Bid dominance 65%",
    ],
    "market_cap": 1_240_000_000_000,
    "foreign_score": 27.0,
    "foreign_status": "FORMAT PREVIEW",
    "net_foreign_value": -490_985_000,
    "net_foreign_ratio_pct": -5.81,
    "foreign_buy_dominance_pct": 44.39,
    "volume_ma_ratio": 2.67,
    "value_ma": 10_610_000_000,
    "shareholder_count": None,
    "swing_low_5d": 74.0,
    "swing_high_5d": 81.0,
    "swing_low_date_5d": "2026-10-01",
    "swing_high_date_5d": "2026-10-05",
    "golden_pocket_lower_5d": 76.45,
    "golden_pocket_upper_5d": 76.67,
    "golden_pocket_lookback_5d": 5,
    "swing_low": 69.0,
    "swing_high": 81.0,
    "swing_low_date": "2026-09-29",
    "swing_high_date": "2026-09-30",
    "golden_pocket_lower": 73.20,
    "golden_pocket_upper": 73.58,
    "golden_pocket_lookback": 20,
    "daily_change_pct": 5.33,
    "interval_return_pct": 1.28,
    "volume_spike": 12.97,
    "value_spike": 1.0,
    "frequency_spike": 1.0,
    "breakout_pct": 1.28,
    "micro_score": 95.0,
    "aggressive_buy_ratio": 1.0,
    "bid_percent": 71.0,
    "reasons": [
        "FORMAT PREVIEW - bukan sinyal perdagangan",
        "Validasi Golden Pocket Tactical 5D dan Structural 20D",
    ],
}

messages = [
    (
        "🧪 FORMAT PREVIEW BOT 4 — SCREENER 1 — BUKAN SINYAL\n\n"
        + format_alert(sample_result)
    ),
    (
        "🧪 BOT 4 — FILTER 2 FALLBACK LIVE — PREVIEW, BUKAN SINYAL\n\n"
        + format_flow_alert(sample_result, 1)
    ),
]

all_ok = True
for index, message in enumerate(messages, start=1):
    result = send_telegram(message)
    print(f"TELEGRAM FORMAT PREVIEW {index} RESULT")
    print(f"ok={result.get('ok')}")
    print(f"sent={result.get('sent')}")
    print(f"failed={result.get('failed')}")

    if result.get("errors"):
        print("ERRORS:")
        for item in result["errors"]:
            print(item["error"])

    all_ok = all_ok and bool(result.get("ok"))

if not all_ok:
    raise SystemExit(1)
