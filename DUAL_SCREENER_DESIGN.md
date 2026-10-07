# Bot 4 Cascading Dual-Screener Design

Bot 4 uses one live scan cycle with a hard cap of six microstructure candidates.
The two screeners are sequential, not parallel.

## Stage 1 — Filter 1 / Momentum Engine

Filter 1 always runs first with the existing Bot 4 signal logic:
- EARLY_IGNITION
- EARLY_LEAKAGE
- PRE_MOMENTUM
- POWERFUL_MOMENTUM

The existing scoring, phase thresholds, hard filters, Golden Pocket context,
alert state, and Telegram format remain the primary path.

Four live microstructure slots are available to Filter 1. If at least one final
valid ticker survives microstructure confirmation and the hard filters, the
cycle stops at Filter 1 and Filter 2 is not run.

## Stage 2 — Filter 2 / Flow Confluence fallback

Filter 2 starts only when Filter 1 finishes the current cycle with zero final
valid tickers. It ranks the universe with a cheap pre-score from data already
fetched and evaluates at most two fallback candidates.

If a fallback ticker was already micro-checked in Stage 1, that result is
reused. Otherwise Filter 2 consumes one of its two fallback microstructure
slots. Total live microstructure candidates therefore never exceed six.

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

Telegram threshold: 75. Flow alerts keep their own 60-minute cooldown and do
not modify Filter 1 phase progression.

## Live cadence and budget

- Every scheduled cycle starts again from Filter 1.
- Filter 2 has no persistent mode and never bypasses Filter 1.
- MICRO_MAX_CANDIDATES remains 6.
- Filter 1 capacity: 4 live microstructure candidates.
- Filter 2 fallback capacity: at most 2 live microstructure candidates.
- No separate workflow or cron job is created for Filter 2.
- The existing six cron-job.org IDX-session schedules continue dispatching
  `.github/workflows/market_scanner.yml` every 5 minutes during regular market
  windows.
- The retired `scanner.yml` endpoint remains absent to prevent duplicate runs.
