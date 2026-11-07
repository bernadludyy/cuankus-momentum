# Cuankus Momentum Bot 4 — ECC pilot instructions

This repository is a live IHSG trading-signal scanner written in Python. The canonical project policy is [AGENTS.md](AGENTS.md); **read and follow it first**. This file supplies a small Claude Code / Everything Claude Code (ECC) entry point, not permission to modify production services.

## Safety boundary

- Pilot scope: source inspection, plans, review, and **offline** regression tests only.
- Do not edit `main.py`, `scanner/`, `project_fabric/`, `storage/`, `bot/`, or any production workflow as part of the ECC pilot.
- Never run `main.py`, manually dispatch a production workflow, contact ZAPI, access production Neon, or send Telegram messages to actual recipients.
- Do not read, print, export, or upload environment secrets. No third-party cloud upload of market data or credentials.
- Preserve the four-minute scanner ceiling, market-hours guard, external Cron-job.org trigger, cache gateway, and Telegram personal/group routing.
- Use an isolated branch and obtain human review before any merge. Do not install ECC, edit hooks, or run remote install scripts autonomously.

## First ECC exercise: Top Gainer Potential routing audit

1. Read `AGENTS.md`, `docs/engineering/BOT4_CODE_MAP.md`, `scanner/top_gainer.py`, `scanner/top_gainer_potential.py`, `bot/notifier.py`, and the relevant sections of `.github/workflows/market_scanner.yml`.
2. Trace the potential Top Gainer alert through scanner -> decision -> delivery; distinguish it from the **disabled** regular Top Gainer digest.
3. Inspect whether destination is strictly private, and whether `FILTER2_ENABLED` and `PTSN_STRUCTURE_ENABLED` remain disabled in the live workflow. Report findings, including file locations; do **not** propose a fix without evidence of a defect.
4. Where available, run source-only Python compilation and `python test_regressions.py` with `PROJECT_FABRIC_DATABASE_URL` empty, `PROJECT_FABRIC_DB_REQUIRED=false`, and `PROJECT_FABRIC_INTEGRATION_TESTS=0`.
5. Describe checks actually executed and their output. If an integration test, local runner, or agent plugin is unavailable, explicitly mark it **not verified**.

## ECC usage

Install the official `ecc@ecc` plugin *in your own coding-agent environment*, after reviewing its hooks and permissions. Official setup: https://github.com/affaan-m/ECC . Do not vendor the entire ECC catalog into this production repo.

With ECC available, start with `/ecc:plan` for a read-only audit plan, then `/code-review` or the `tdd-workflow` skill if and only if a concrete bug is confirmed. The project-local skill `.agents/skills/bot4-ecc-audit/SKILL.md` describes the bounded exercise. Not all slash commands are available on every supported agent.

See [ECC pilot guide](docs/engineering/ECC_PILOT.md) for prerequisites, checks, and expected limits.
