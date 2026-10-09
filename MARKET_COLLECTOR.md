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
