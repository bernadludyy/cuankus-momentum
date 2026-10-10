from bot.notifier import send_telegram
from main import format_alert


sample_result = {
    # Verified Filter 1 replay from Friday, 9 Oct 2026, 14:16 WIB.
    "phase": "PRE_MOMENTUM",
    "ticker": "SRSN",
    "price": 139.0,
    "score": 72.0,
    "quality_score": None,  # V2 quality layer was not yet recorded on Friday.
    "leakage_score": None,
    "momentum_score": None,
    "ignition_score": None,
    "market_cap": 806_680_000_000,
    "foreign_score": 0.0,
    "foreign_status": "STRONG FOREIGN SELLING",
    "net_foreign_value": None,
    "net_foreign_ratio_pct": None,
    "foreign_buy_dominance_pct": None,
    "volume_ma_ratio": 2.2174,
    "value_ma": 50_036_696_220,
    "transaction_value_ma50": 31_567_262_634,
    "daily_change_pct": 3.731343283582089,
    "interval_return_pct": 1.4598540145985384,
    "volume_spike": 5.046702813280848,
    "value_spike": None,
    "frequency_spike": 1.0,
    "breakout_pct": None,
    "micro_score": 60.0,
    "aggressive_buy_ratio": 0.9223,
    "aggressive_buy_value": None,
    "aggressive_sell_value": None,
    "net_aggression_value": None,
    "aggression_trend": None,
    "selling_absorption": "NONE",
    "bid_percent": 36.0,
    "reasons": [
        "REPLAY data Filter 1 Jumat 9 Oktober 2026 pukul 14:16 WIB",
        "Tx Value MA50 dihitung dari stock-history 50 sesi yang tersimpan",
        "Field Tradeflow V2 yang belum direkam pada Jumat ditampilkan sebagai '-'",
    ],
}

message = (
    "🧪 REPLAY BOT 4 FILTER 1 — DATA JUMAT 9 OKTOBER 2026\n"
    "⚠️ BUKAN SINYAL LIVE / BUKAN ALERT BARU\n\n"
    + format_alert(sample_result, lane="FILTER 1 — STRICT V2 REPLAY")
)

result = send_telegram(message, target="private")
print("TELEGRAM FILTER 1 FRIDAY REPLAY RESULT")
print(f"ok={result.get('ok')}")
print(f"sent={result.get('sent')}")
print(f"failed={result.get('failed')}")

if result.get("errors"):
    print("ERRORS:")
    for item in result["errors"]:
        print(item["error"])

if not result.get("ok"):
    raise SystemExit(1)
