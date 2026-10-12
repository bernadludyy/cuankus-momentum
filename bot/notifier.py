import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_GROUP_CHAT_ID,
    validate_telegram_config,
)


_MIGRATED_CHAT_IDS: dict[str, str] = {}


def _extract_migrate_to_chat_id(response) -> str | None:
    try:
        body = response.json()
    except Exception:
        return None
    migrated = (
        body.get("parameters", {}).get("migrate_to_chat_id")
        if isinstance(body, dict)
        else None
    )
    return str(migrated) if migrated is not None else None


def _send_to_chat(message: str, chat_id: str) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    original_chat_id = str(chat_id)
    effective_chat_id = _MIGRATED_CHAT_IDS.get(
        original_chat_id,
        original_chat_id,
    )

    payload = {
        "chat_id": effective_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    response = requests.post(url, data=payload, timeout=20)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        migrated_chat_id = _extract_migrate_to_chat_id(response)
        if (
            migrated_chat_id
            and migrated_chat_id != effective_chat_id
        ):
            _MIGRATED_CHAT_IDS[original_chat_id] = migrated_chat_id
            retry_payload = dict(payload)
            retry_payload["chat_id"] = migrated_chat_id
            retry = requests.post(url, data=retry_payload, timeout=20)
            retry.raise_for_status()
            result = retry.json()
            if isinstance(result, dict):
                result["_migrated_from_chat_id"] = original_chat_id
                result["_migrated_to_chat_id"] = migrated_chat_id
            print(
                "[TELEGRAM-GROUP-MIGRATED] "
                "Telegram upgraded the group to a supergroup; "
                "delivery retried with migrate_to_chat_id."
            )
            return result
        raise

    return response.json()


def get_telegram_targets(target: str = "all") -> list[str]:
    """Route explicitly: structural PTSN alerts must never reach the group."""
    if target not in {"all", "private"}:
        raise ValueError(f"Unknown Telegram target: {target}")
    targets = [TELEGRAM_CHAT_ID]
    if target == "private":
        return [chat_id for chat_id in targets if chat_id]

    if (
        TELEGRAM_GROUP_CHAT_ID
        and TELEGRAM_GROUP_CHAT_ID not in targets
    ):
        targets.append(TELEGRAM_GROUP_CHAT_ID)

    return [chat_id for chat_id in targets if chat_id]


def send_telegram(message: str, target: str = "all") -> dict:
    """Send to both destinations by default, or only to the private chat."""
    validate_telegram_config()

    results = []
    errors = []

    for chat_id in get_telegram_targets(target=target):
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
