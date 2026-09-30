from __future__ import annotations

from datetime import datetime, time, timedelta, timezone


WIB = timezone(timedelta(hours=7))


def is_idx_market_window(now: datetime | None = None) -> bool:
    """Regular-session guard in WIB.

    Holiday awareness is intentionally not inferred here; on exchange holidays
    the upstream market data should remain unchanged and no momentum signal
    should be generated.
    """
    now = now or datetime.now(WIB)
    if now.tzinfo is None:
        now = now.replace(tzinfo=WIB)
    else:
        now = now.astimezone(WIB)

    weekday = now.weekday()  # Mon=0 ... Sun=6
    current = now.time()

    if weekday > 4:
        return False

    if weekday <= 3:  # Mon-Thu
        morning = time(9, 0) <= current <= time(12, 0)
        afternoon = time(13, 30) <= current <= time(15, 50)
        return morning or afternoon

    # Friday
    morning = time(9, 0) <= current <= time(11, 30)
    afternoon = time(14, 0) <= current <= time(15, 50)
    return morning or afternoon
