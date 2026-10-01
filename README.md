# Cuankus Momentum — Bot 4

Bot 4 is a stricter clone of Bot 3 (Powerful Momentum).

Active extra filters before microstructure:
- Price < 500
- Market cap > Rp100B and < Rp10T
- Volume MA10 >= 2 x Volume MA100
- Value MA5 > Rp500,000,000
- Number of Shareholders < 5,000 (implemented; enable after smoke test)

Daily MA inputs come from IDX stock-history and use completed trading days only.
The current intraday session is excluded so partial-day volume/value do not distort the MA.
Metrics are cached locally for 18 hours to reduce ZAPI usage.

Shareholder count is sourced from the latest official IDX Monthly Report of
Securities Holders Registration (LBRE). Announcement metadata is retrieved via
the existing ZAPI IDX company-announcements endpoint and the attached IDX PDF is
parsed locally. Successful values are cached for 7 days; failed lookups for 6
hours to avoid repeated requests.

Bot 3 remains unchanged. Bot 4 uses its own Telegram bot/group, database/cache,
workflow identity, and cron trigger.


## Core scanner fixes inherited from Bot 3

- Breakout 0.0 is treated as valid data, not as missing.
- PRE_MOMENTUM is evaluated ahead of EARLY_LEAKAGE after micro confirmation.
- Two Ignition microstructure slots are genuinely reserved, then reclaimed if unused.
- Re-arm state distinguishes NO_SIGNAL, PENDING_MICRO, NOT_EVALUATED, and API_FAILED.
- Running-trade confirmation only uses prints from the latest 15 minutes.
- Missing cumulative Frequency is backfilled from IDX stock-summary when available.
- Regression tests run on every workflow execution.

The shareholder hard filter remains disabled in the live workflow until its IDX-PDF
smoke test succeeds reliably.
