# Cuankus Momentum — Bot 4

Bot 4 is a stricter clone of Bot 3 (Powerful Momentum).

Active extra filters before microstructure:
- Price < 500
- Market cap > Rp100B and < Rp10T
- Volume MA10 >= 2 x Volume MA100
- Value MA5 > Rp500,000,000

Daily MA inputs come from IDX stock-history and use completed trading days only.
The current intraday session is excluded so partial-day volume/value do not distort the MA.
Metrics are cached locally for 18 hours to reduce ZAPI usage.

Pending filter:
- Number of Shareholders < 5,000
  This remains disabled until a reliable per-ticker shareholder-count source is confirmed.

Bot 3 remains unchanged. Bot 4 uses its own Telegram bot/group, database/cache,
workflow identity, and cron trigger.
