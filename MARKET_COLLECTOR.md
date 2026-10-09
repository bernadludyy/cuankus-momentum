# Shared Market Collector V1

## Goal

Use one common ZAPI market snapshot across Bot 4, Bot 1, and future consumers
without creating another high-frequency GitHub Actions workflow.

## Runtime flow

1. Existing Bot 4 market workflow is triggered by cron-job.org.
2. Bot 4 calls `collect_shared_market_snapshot(length=5000)`.
3. Project Fabric DataGateway requests IDX `stock-summary` only when the shared
   cache is stale.
4. The payload is stored in Neon table `project_fabric.cache_entries` under
   namespace `shared-v1`.
5. Bot 4 uses the exact same payload for dynamic universe discovery.
6. Bot 1 Telegram commands connect to the same Neon database and namespace.
   Matching endpoint+parameter requests are served from the shared cache.
7. When the cache is older than the freshness policy, the requesting service may
   refresh ZAPI and republish the cache.

## Freshness

- IDX stock-summary fresh TTL: 300 seconds.
- Stale-if-upstream-error window: 900 seconds.
- Live quote, orderbook, and running-trades remain uncached by the shared
  collector because they are confirmation data that should stay live.

## Cost controls

V1 intentionally does not create a dedicated collector workflow or extra cron
job. The existing Bot 4 run performs collection before scanning, so GitHub
Actions run count does not increase.

## Current consumers

- Bot 4: producer + consumer of full IDX stock-summary.
- Bot 1 daily/update path: shared Project Fabric cache enabled.
- Bot 1 Telegram query path: shared Project Fabric cache enabled by this phase.

## Next candidates

After V1 is stable, common endpoints can be standardized one by one:
- stock-history with a shared length contract;
- foreign-flow where command freshness permits;
- broker-summary with ticker/date keyed cache and an appropriate TTL.

Orderbook and running-trades should remain candidate-only live calls.


## Phase 2 — shared history, foreign flow, and broker cache

Phase 2 extends the cache-first contract without adding another scheduled job.

### Stock history

- Canonical shared request length: 120 sessions per ticker.
- Fresh TTL: 6 hours.
- Stale-on-error: 24 hours.
- Bot 4 already requests 120 sessions.
- Bot 1 also requests 120 upstream, then trims the cached payload locally for
  1D/5D/10D/20D command output.
- Result: one ticker/history cache key can serve both Bot 4 and Bot 1.

### Foreign flow

- Current/latest request TTL: 5 minutes.
- Historical dated request TTL: 6 hours.
- Historical stale-on-error: 24 hours.
- First-page leaderboard requests use a canonical length of 50 so different
  Telegram display limits reuse the same cache key.

### Broker summary

- Current-session broker-summary TTL: 5 minutes.
- Historical completed-session TTL: 6 hours.
- Historical stale-on-error: 24 hours.
- Cache key remains ticker + startDate + endDate + net flag, so different
  securities and periods cannot collide.

### Historical stock-summary

Dated stock-summary requests now use the 6-hour historical policy. This is
especially useful for Bot 1 multi-day foreign aggregation, which should not
re-fetch completed trading days every few minutes.

### Remains live

The following confirmation endpoints are deliberately not cached:
- quote;
- summary/multi-quote;
- orderbook;
- running-trades.

This keeps Bot 4 microstructure confirmation live while moving reusable
historical/common data into Neon.
