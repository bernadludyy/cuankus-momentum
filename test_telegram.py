from bot.notifier import _send_to_chat
from config import TELEGRAM_GROUP_CHAT_ID


if not TELEGRAM_GROUP_CHAT_ID:
    print("GROUP-DIAG: TELEGRAM_GROUP_CHAT_ID is empty")
    raise SystemExit(2)

try:
    result = _send_to_chat(
        "🧪 BOT 4 GROUP DELIVERY CHECK\nPesan diagnostik grup — bukan sinyal trading.",
        TELEGRAM_GROUP_CHAT_ID,
    )
    print("GROUP-DIAG: SUCCESS")
    print(f"ok={result.get('ok')}")
except Exception as exc:
    print("GROUP-DIAG: FAILED")
    print(f"type={type(exc).__name__}")
    print(f"error={exc}")
    response = getattr(exc, "response", None)
    if response is not None:
        print(f"status={getattr(response, 'status_code', None)}")
        try:
            print(f"body={response.text}")
        except Exception:
            pass
    raise
