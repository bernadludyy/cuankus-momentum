# Bot 4 Dual-Screener Design

Bot 4 keeps one shared market-data pass and a hard cap of six live
microstructure candidates per scan. The second screener does not increase the
orderbook/running-trades request budget.

## Screener 1 — Momentum Engine

This is the existing Bot 4 logic and thresholds:
- EARLY_IGNITION
- EARLY_LEAKAGE
- PRE_MOMENTUM
- POWERFUL_MOMENTUM

Four of the six microstructure slots are reserved for the legacy momentum
engine. Existing scoring, phase thresholds, hard filters, Golden Pocket
context, and alert progression are preserved.

## Screener 2 — Flow Confluence

Two of the same six microstructure slots are reserved for two distinct tickers
ranked by a cheap pre-score computed from data already fetched for the universe.

### Flow pre-score (0–100)

- Foreign Score: 40 points
- Activity Spike: 25 points
- Volume Spike: 20 points
- Position in intraday range: 10 points
- Positive interval return: 5 points

Minimum pre-score: 50.

### Final Flow Score (0–100)

- Foreign Score: 30 points
- Microstructure Score: 25 points
- Aggressive Buy: 15 points
- Bid Dominance: 10 points
- Volume Spike: 10 points
- Activity Spike: 10 points

Status:
- >=85: STRONG FLOW CONFLUENCE
- >=75: FLOW CONFLUENCE
- >=65: FLOW WATCH
- <65: NONE

Telegram threshold: 75.
Flow alerts have their own 60-minute cooldown and do not modify Screener 1
phase progression.

## API and Actions budget

- MICRO_MAX_CANDIDATES stays 6.
- Screener 1 owns 4 slots.
- Screener 2 owns 2 unique slots.
- No extra orderbook/running-trades slots are added.
- Unused slots are reclaimed by the other pool, but the total never exceeds 6.
- The scanner is dispatched only by the six exact IDX-session cron jobs.
- Production workflow: `.github/workflows/market_scanner.yml`.
- The retired `scanner.yml` endpoint is intentionally absent to block old
  duplicate cron jobs from creating Actions runs.
