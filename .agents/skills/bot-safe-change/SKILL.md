---
name: bot-safe-change
description: Guardrail workflow for changing production bots that use APIs, databases, Telegram, or scheduled CI. Use before implementing or deploying a bot change.
---

# Bot Safe Change

Apply this gate before modifying a production bot.

## 1. Classify impact

Record whether the change touches runtime logic, ZAPI/API calls, Neon/cache schema, Telegram routing, schedules, secrets, or scoring. If none apply, state why the change is non-runtime.

## 2. Pin invariants

List behavior that must not change. For trading bots this normally includes market-hours guards, API/cache boundaries, recipient separation, alert deduplication, EOD versus intraday semantics, and usage limits.

## 3. Work in isolation

Use a branch. Never test with production credentials or send real alerts. Do not trigger production workflows merely to validate documentation or agent tooling.

## 4. Prove the change

Use the narrowest offline check that exercises the changed behavior. Add a regression test for bug fixes and decision logic. Record commands and outputs.

## 5. Deployment gate

Before merge, state rollback method, blast radius, config or migration needs, and whether GitHub Actions or API usage increases. Any unknown must be resolved or explicitly block deployment.
