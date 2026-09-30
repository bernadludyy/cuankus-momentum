from bot.notifier import send_telegram

message = """🚀 POWERFUL MOMENTUM BOT

Telegram delivery test ✅

Tujuan:
• Private chat
• Group chat (jika TELEGRAM_GROUP_CHAT_ID tersedia)

Bot 3 siap mengirim Early Leakage dan Powerful Momentum.
"""

result = send_telegram(message)

print("TELEGRAM DELIVERY RESULT")
print(f"ok={result.get('ok')}")
print(f"sent={result.get('sent')}")
print(f"failed={result.get('failed')}")

if result.get("errors"):
    print("ERRORS:")
    for item in result["errors"]:
        print(item["error"])
