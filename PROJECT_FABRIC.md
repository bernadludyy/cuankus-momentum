# Project Fabric v1

Project Fabric is the shared engineering foundation for this repository and the rest of the bot ecosystem.

## Goals

- Keep existing strategy/business logic intact.
- Standardize configuration parsing, retries, validation, caching, Jakarta time handling, and structured telemetry.
- Prevent uncontrolled retry loops and duplicate infrastructure code.
- Make API usage easier to reduce through TTL caching.
- Prepare a clean migration path from runner-local state/SQLite to a persistent cloud database.
- Add a preflight check to GitHub Actions without changing signal logic.

## Runtime modules

- `project_fabric.core.env_bool/env_int`: strict environment parsing.
- `project_fabric.core.ResilientHttp`: bounded GET/HEAD retries with Retry-After support.
- `project_fabric.core.JsonTTLCache`: atomic TTL cache for stock-summary, metadata, and other reusable payloads.
- `project_fabric.core.in_time_windows`: reusable Asia/Jakarta session guard.
- `project_fabric.core.as_float`: defensive numeric normalization.
- `project_fabric.core.emit`: JSON telemetry with basic secret redaction.
- `project_fabric.core.RunMetrics`: counters for API calls, cache hits, alerts, errors, and elapsed time.
- `python -m project_fabric.selfcheck`: zero-network preflight check.

## Rollout contract

Phase 1 (this PR) is deliberately non-breaking: add the framework, tests, CI, and a workflow preflight check. Existing bot logic remains unchanged.

Phase 2: COMPLETE. Market-data reads now pass through `DataGateway` with bounded retries, endpoint-specific freshness, short-lived cache, stale-on-error only for explicitly safe reference data, and per-run API telemetry. Live quote/orderbook/running-trades remain uncached. GitHub Actions restores/saves only the cache directory and uploads the usage report separately.

Phase 3: introduce `DATABASE_URL` with PostgreSQL/Neon as persistent production storage while retaining SQLite as local fallback. The Phase 2 gateway is the compatibility boundary: its cache/metrics backend can move from runner-local files to PostgreSQL without changing strategy code.

Phase 4: central dashboard/observability can read the persistent run, API-usage, signal, and delivery tables.

## Example

```python
from project_fabric.core import JsonTTLCache, ResilientHttp

http = ResilientHttp(retries=2)
cache = JsonTTLCache()

payload = cache.get("stock-summary", ttl_seconds=60)
if payload is None:
    payload = http.request_json("GET", "https://example.invalid/data")
    cache.set("stock-summary", payload)
```

Do not cache highly time-sensitive quote/orderbook data unless the strategy explicitly defines a very short TTL. Do not automatically retry Telegram POST delivery unless an idempotency mechanism is present.


## Phase 2 freshness contract

| Family | Endpoint | Fresh TTL | Stale fallback |
|---|---|---:|---:|
| IDX | stock-summary | 120s | 600s |
| IDX | stock-history | 6h | 24h |
| IDX | foreign-flow | 120s | 600s |
| IDX | company-announcements | 6h | 24h |
| IDX | top-movers | 30s | 120s |
| Pluang | summary / quote | 0 | disabled |
| Pluang | orderbook | 0 | disabled |
| Pluang | running-trades | 0 | disabled |

The stale fallback is only used after an upstream failure. It is never enabled for live execution or microstructure endpoints.


## Phase 3 shared-cache architecture

All repositories can share compatible cache entries through the same PostgreSQL/Neon database by configuring the same `PROJECT_FABRIC_DATABASE_URL` secret and `PROJECT_FABRIC_CACHE_NAMESPACE=shared-v1`.

The shared schema stores cache entries, distributed leases, and API usage runs under `project_fabric`. Local JSON remains a resilience mirror/fallback; live quote/orderbook/running-trades remain uncached.


## Phase 4 observability

Phase 4 adds a durable control plane on top of the shared Neon database.

Production workflows now publish:
- run lifecycle into `project_fabric.bot_runs`;
- component health into `project_fabric.health_checks`;
- strategy/business signals into `project_fabric.signal_events`;
- delivery outcomes into `project_fabric.delivery_events`.

The Cuankus Momentum runtime requires the shared database before execution and keeps secrets out of the observability payload. Telegram destinations are hashed rather than stored in plaintext.

A production probe workflow writes verification-only events and validates all four Phase 4 tables. Verification rows are marked and excluded from the central dashboard.

The central Control Center lives in `powerfull-momentum-bot/control_center` and reads all five services from the same Neon database. Its GitHub Actions workflow produces a self-contained `index.html` plus `snapshot.json` artifact every 15 minutes and on relevant Control Center changes.
