# AI engineering skills — Bot 4 (development only)

Purpose: make code review and maintenance safer **without increasing ZAPI calls or scanner scheduling**. This is a project integration guide, not a record that external plugins have been executed or installed.

## Baseline (verified from repository source on 2026-10-14)

- Production: `.github/workflows/market_scanner.yml` is dispatched externally and has `timeout-minutes: 4`.
- Existing CI: `.github/workflows/project_fabric_ci.yml` runs separate test jobs on pull requests and manual dispatch.
- `main.py` uses scanner, cache, Project Fabric and Telegram modules. Do not add skill installations to `market_scanner.yml`.
- `scanner/top_gainer.py` and `scanner/top_gainer_potential.py` are designed to consume existing data without extra upstream market calls.

## 1. Graphify — dependency mapping, on demand

Source: https://github.com/Graphify-Labs/graphify (use official install instructions; CLI package is `graphifyy`).

On a local development clone, install the CLI in an isolated tool environment (Python 3.10+):

```bash
uv tool install graphifyy
graphify install --project --platform codex
```

Or use `pipx install graphifyy` instead of `uv tool install graphifyy`. Open a fresh session of your supported coding assistant and run:

```text
/graphify .
```

Expected local outputs include `graphify-out/graph.html`, `graphify-out/GRAPH_REPORT.md`, and `graphify-out/graph.json`. The output directory is intentionally gitignored. Graphify parses source locally; avoid enabling optional external cloud or semantic document processing for this repository without reviewing what data will leave the environment.

Suggested investigation questions:

- Trace `main.py` -> `scanner/market_data.py` -> `DataGateway.get_json` and cache fallback.
- Identify the path from EOD stock summary to `eod_finalizer.py`, `market_archive.py`, and shared Neon data.
- Locate all callers of `send_*` methods before altering private versus group Telegram alerts.
- Identify any duplicated extraction/normalization logic **after** measuring correctness with existing regression tests.

## 2. Superpowers — root-cause debugging and verification

Source: https://github.com/obra/superpowers. Install in your coding-agent environment using that project's official plugin instructions (for example, the Superpowers plugin available through supported Codex interfaces). It is **not** a Python production dependency.

Use its `systematic-debugging` and `verification-before-completion` approach:

1. Record the symptom, affected ticker/date, and expected versus actual outcome.
2. Trace workflow -> environment -> data gateway -> cache freshness -> scanner -> notification, capturing facts rather than guessing.
3. Create an offline failing regression test with stubbed ZAPI, Telegram, and Neon.
4. Implement one root-cause fix, re-run the affected regression, then run the broader suite.
5. Report test results and any unverified integration step; do not describe an untested production fix as successful.

Examples: empty universe, stale EOD, same-phase repeated alerts, Telegram private/group misrouting.

## 3. Ponytail — low-risk simplification

Source: https://github.com/DietrichGebert/ponytail. Prefer the official coding-agent plugin. Codex CLI example (where supported):

```bash
codex plugin marketplace add DietrichGebert/ponytail
codex plugin add ponytail@ponytail
```

Alternatively the repository `AGENTS.md` adopts its minimal-change principles in a lightweight, agent-independent form. A project-level guide is **not** a substitute for an installed Ponytail plugin.

Review targets (suggestions, not pre-approved refactors):

- Redundant imports or wrappers in `main.py`.
- Duplicated number/date parsing in `scanner/top_gainer*.py`, `project_fabric/market_archive.py`, and EOD modules: **do not consolidate blindly**, because feed contracts differ.
- Avoid adding a second network client or bypassing `DataGateway`.
- Avoid introducing another cron task merely for static analysis.

## Verification, cost and rollout

| Action | GitHub Actions minutes | ZAPI calls |
| --- | --- | --- |
| Run Graphify on local checkout | 0 | 0 |
| Load Superpowers/Ponytail into local coding agent | 0 | 0 |
| Run offline tests locally with mocks | 0 | 0 |
| Open a PR / run GitHub-hosted CI | Uses CI minutes | 0 when tests are offline |
| Run manual scanner/API/Telegram smoke tests | Uses Actions minutes if hosted | May make real calls; opt-in only |

**Sequence**: inspect the initial code map -> generate optional Graphify graph locally -> take one bounded bug or simplification -> write failing test -> verify on isolated branch -> review diff -> approve PR/merge separately. Never auto-merge an engineering audit into production.

**Status at initial setup:** project instructions and a manually reviewed map can be committed in a separate branch. The three external plugins and a Graphify-generated graph have **not** been executed by merely adding these files.
