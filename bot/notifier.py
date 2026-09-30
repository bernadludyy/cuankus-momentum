import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_GROUP_CHAT_ID,
    validate_telegram_config,
)


def _send_to_chat(message: str, chat_id: str) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    response = requests.post(url, data=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def get_telegram_targets() -> list[str]:
    targets = [TELEGRAM_CHAT_ID]

    if (
        TELEGRAM_GROUP_CHAT_ID
        and TELEGRAM_GROUP_CHAT_ID not in targets
    ):
        targets.append(TELEGRAM_GROUP_CHAT_ID)

    return [chat_id for chat_id in targets if chat_id]


def send_telegram(message: str) -> dict:
    """Kirim alert ke private chat dan, jika diset, ke grup Telegram."""
    validate_telegram_config()

    results = []
    errors = []

    for chat_id in get_telegram_targets():
        try:
            result = _send_to_chat(message, chat_id)
            results.append(
                {
                    "chat_id": chat_id,
                    "ok": bool(result.get("ok")),
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "chat_id": chat_id,
                    "error": str(exc),
                }
            )

    return {
        "ok": bool(results) and not errors and all(x["ok"] for x in results),
        "sent": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }
