# Bot 4 engineering rules for AI coding agents

This is a **production IHSG scanner**. Follow the project conventions before using external AI skills.

## Guardrails

- Keep `main.py`, `scanner/`, `project_fabric/`, `storage/`, `.github/workflows/market_scanner.yml`, and the Telegram recipients unchanged unless a task explicitly calls for a runtime change.
- Preserve the 4-minute live scanner timeout, its IDX market-hours guard, and its existing cache/DataGateway strategy.
- Never execute the live scanner, call ZAPI, send real Telegram messages, use production Neon credentials, or trigger `workflow_dispatch` merely for a code review.
- Do not add Graphify, Superpowers, Ponytail, or AI inference dependencies to `requirements.txt` or the live GitHub Actions workflow. These are **developer tooling**, not runtime dependencies.
- Keep new tool output local. Never commit `.env`, `.runtime/`, `data/`, or `graphify-out/` artifacts.
- Prefer an isolated branch. For changes touching scores, EOD freshness, money, alert deduplication, or routing, add relevant offline regression tests before changing logic.
- Use `.agents/skills/bot-output-quality/SKILL.md` when editing code comments, Telegram alerts, command responses, or operator documentation. Preserve source dates, live/EOD labels, units, and the separation between facts, interpretation, and suggested action.

## Scope and data flow

- **Live entry:** `.github/workflows/market_scanner.yml` -> `main.py` -> scanner modules. Cron-job.org dispatches the workflow externally.
- **API/cache boundary:** `scanner/market_data.py` -> `project_fabric/data_gateway.py` -> local cache / Neon PostgreSQL. Do not bypass the gateway with new ZAPI requests.
- **EOD:** `.github/workflows/eod_universe_finalizer.yml` -> `project_fabric/eod_finalizer.py` -> `project_fabric/market_archive.py` and universe intelligence. Never conflate partial intraday candles with a completed EOD session.
- **Alerts:** `main.py`, `scanner/engine.py`, `scanner/top_gainer.py`, `scanner/top_gainer_potential.py`, `bot/notifier.py`, `storage/db.py`. Preserve the distinct private/group recipients.
- See `docs/engineering/BOT4_CODE_MAP.md` for an initial **hand-verified map**; it is not a Graphify-generated graph.

## Three complementary external skills

1. **Graphify** (`https://github.com/Graphify-Labs/graphify`): build the code dependency graph **locally and on demand** before changing cross-module data flow. Do not launch it inside the live workflow.
2. **Superpowers** (`https://github.com/obra/superpowers`): follow systematic debugging: reproduce -> locate root cause -> add failing test -> minimal fix -> fresh verification. Do not claim a fix worked without a test or equivalent evidence.
3. **Ponytail** (`https://github.com/DietrichGebert/ponytail`): first inspect existing helpers and all callers; prefer the smallest complete change, preserving validations, error handling, cache freshness, and security boundaries.

Official installation instructions and the non-production workflow are in `docs/engineering/AI_SKILLS_SETUP.md`. These instructions do **not** imply that the third-party plugins are installed in a user's coding agent.

## Offline verification (development workspace only)

After installing the repository requirements and without production credentials:

```bash
PROJECT_FABRIC_DATABASE_URL='' PROJECT_FABRIC_DB_REQUIRED=false PROJECT_FABRIC_INTEGRATION_TESTS=0 python -m py_compile main.py scanner/*.py project_fabric/*.py storage/*.py bot/*.py
PROJECT_FABRIC_DATABASE_URL='' PROJECT_FABRIC_DB_REQUIRED=false PROJECT_FABRIC_INTEGRATION_TESTS=0 python test_regressions.py
```

Project Fabric database integration tests require an isolated PostgreSQL test database; run them only in a deliberately configured test environment. Do not run `test_telegram.py`, API smoke tests, or the production workflow as a side effect of auditing.

Report exactly which checks ran, their output, and what could not be verified.
