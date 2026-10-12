from bot.notifier import _send_to_chat
from config import TELEGRAM_GROUP_CHAT_ID


if not TELEGRAM_GROUP_CHAT_ID:
    print("GROUP-FIX-VERIFY: TELEGRAM_GROUP_CHAT_ID is empty")
    raise SystemExit(2)

try:
    result = _send_to_chat(
        "✅ BOT 4 GROUP DELIVERY FIX TEST\nSupergroup migration fallback berhasil.",
        TELEGRAM_GROUP_CHAT_ID,
    )
    print("GROUP-FIX-VERIFY: SUCCESS")
    print(f"ok={result.get('ok')}")
    print(f"migrated={bool(result.get('_migrated_to_chat_id'))}")
except Exception as exc:
    print("GROUP-FIX-VERIFY: FAILED")
    print(f"type={type(exc).__name__}")
    print(f"error={exc}")
    raise
