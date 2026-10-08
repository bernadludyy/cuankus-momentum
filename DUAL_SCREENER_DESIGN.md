# Bot 4 Cascading Dual-Filter Design

Bot 4 runs one live scan cycle every scheduled interval. Filter 1 and Filter 2
are sequential, not parallel.

## Stage 1 — Filter 1 / Primary Momentum

Filter 1 remains unchanged and keeps the strict Bot 4 logic:
- EARLY_IGNITION
- EARLY_LEAKAGE
- PRE_MOMENTUM
- POWERFUL_MOMENTUM

Primary thresholds, Golden Pocket context, hard filters, alert progression, and
state handling are preserved.

Filter 1 gets up to four live microstructure slots. If at least one final valid
ticker survives, Filter 2 is skipped for that cycle.

## Stage 2 — Filter 2 / Relaxed Momentum fallback

Filter 2 starts only when Filter 1 returns zero final valid tickers.

Filter 2 uses the same momentum feature family as Filter 1:
- price and interval movement
- volume/value/frequency activity
- breakout / position in intraday range
- orderbook microstructure
- aggressive buying
- leakage / momentum / ignition scores

It is intentionally more permissive, but does not relax falling-knife safety.

### Relaxed thresholds

Default Filter 2 settings:
- pre-score candidate floor: 25 vs Filter 1 micro pre-score 35
- daily VolMA10/MA100: >=1.20x vs Filter 1 >=2.00x
- leakage confirmation: >=60 vs Filter 1 >=70
- momentum confirmation: >=70 vs Filter 1 >=80
- powerful-momentum breakout: >=0.00% vs Filter 1 >=0.30%
- powerful-momentum interval return: >=0.15% vs Filter 1 >=0.35%
- pre-momentum confirmation: >=55 vs Filter 1 >=65
- micro confirmation: >=40 vs Filter 1 >=45
- pre-momentum micro: >=45 vs Filter 1 >=55
- aggressive buy for pre-momentum: >=55% vs Filter 1 >=60%
- ignition volume: >=2.0x vs Filter 1 >=3.0x
- ignition micro: >=45 vs Filter 1 >=55
- ignition aggressive buy: >=55% vs Filter 1 >=65%

Price <500, market cap >Rp100B and <Rp10T, minimum Value MA5, and shareholder
filter remain the same.

### Anti-falling-knife remains hard

Filter 2 must still pass the original anti-falling-knife and overextension
flags. In addition, when intraday range position is available, price must be at
least 30% above the session low-to-high range. A candidate cannot become a
Filter 2 alert merely because thresholds were relaxed while it is still
behaving like a falling knife.

## Live cadence and API budget

- Every cycle always starts from Filter 1.
- Filter 2 runs only when Filter 1 has zero final candidates.
- The next cycle starts from Filter 1 again.
- Filter 1 capacity: up to 4 microstructure candidates.
- Filter 2 fallback capacity: up to 2 microstructure candidates.
- Total live microstructure budget remains max 6 candidates per cycle.
- Existing Filter 1 microstructure results are reused by Filter 2 when the same
  ticker is selected.
- No separate GitHub workflow or cron job is needed.
- Existing cron-job.org Session A-F continue dispatching
  `.github/workflows/market_scanner.yml` every 5 minutes in IDX market windows.
- Filter 2 has an independent 60-minute cooldown and does not alter Filter 1
  phase state.
