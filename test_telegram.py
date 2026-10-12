from bot.notifier import send_telegram
from main import format_alert


sample_result = {
    "phase": "PRE_MOMENTUM",
    "ticker": "BEKS",
    "price": 33.0,
    "score": 68.3,
    "quality_score": 65.2,
    "leakage_score": 30.1,
    "momentum_score": 68.3,
    "ignition_score": None,
    "market_cap": 1_690_000_000_000,
    "foreign_score": 7.8,
    "foreign_status": "STRONG FOREIGN SELLING",
    "net_foreign_value": None,
    "net_foreign_ratio_pct": None,
    "foreign_buy_dominance_pct": None,
    "volume_ma_ratio": 7.49,
    "value_ma": 15_790_000_000,
    "transaction_value_ma50": 2_550_000_000,
    "daily_change_pct": 0.0,
    "interval_return_pct": 3.12,
    "volume_spike": 3.81,
    "value_spike": 1.0,
    "frequency_spike": 1.0,
    "breakout_pct": 3.12,
    "micro_score": 66.2,
    "aggressive_buy_ratio": 0.8122,
    "aggressive_buy_value": 438_610_000,
    "aggressive_sell_value": 101_370_000,
    "net_aggression_value": 337_240_000,
    "aggression_trend": "INSUFFICIENT_HISTORY",
    "selling_absorption": "NONE",
    "bid_percent": 51.0,
    "anti_falling_knife_relaxed_pass": True,
    "not_overextended": True,
    "filter2_relaxed": True,
    "reasons": [
        "PREVIEW entry reversal/pullback dari data BEKS",
        "Bukan sinyal baru dan bukan instruksi transaksi otomatis",
    ],
}

message = (
    "🧪 PREVIEW BOT 4 — ENTRY REVERSAL / PULLBACK\n"
    "⚠️ BUKAN SINYAL BARU\n\n"
    + format_alert(sample_result, rank=1)
)

result = send_telegram(message)
print("ENTRY PREVIEW RESULT")
print(f"ok={result.get('ok')}")
print(f"sent={result.get('sent')}")
print(f"failed={result.get('failed')}")
if result.get("errors"):
    for item in result["errors"]:
        print(item["error"])
if not result.get("ok"):
    raise SystemExit(1)
