---
name: bot4-ecc-audit
description: "Read-only, offline ECC pilot for Cuankus Momentum Bot 4: audit Top Gainer Potential private routing and disabled legacy alerts without production API, Telegram, or Neon access."
metadata:
  origin: bot4-local
---

# Bot 4 ECC offline audit

This is a **project-local skill**, inspired by ECC's plan -> test -> review -> verify workflow. It is NOT an installed ECC plugin and does not execute itself. Require a coding agent with the skill installed or discoverable to invoke it.

## Read-only scope

1. Read `AGENTS.md` and `CLAUDE.md`, then `docs/engineering/BOT4_CODE_MAP.md`.
2. Map the **current** call chain in `main.py`, `scanner/top_gainer_potential.py`, `scanner/top_gainer.py`, `bot/notifier.py` and `.github/workflows/market_scanner.yml`.
3. Verify that the live configuration has `TOP_GAINER_ENABLED: "false"`, `TOP_GAINER_POTENTIAL_ENABLED: "true"`, `TOP_GAINER_TARGET: "private"`, `FILTER2_ENABLED: "false"`, and `PTSN_STRUCTURE_ENABLED: "false"`. Configuration alone is not proof of correct runtime delivery: inspect the real dispatch path.
4. Check for a counterexample: could the regular Top Gainer digest still be called? Could Top Gainer Potential be routed to the group? Cite the code path, not a guess.
5. Inspect tests in `test_regressions.py` and `tests/test_project_fabric_top_gainer_potential.py`, `tests/test_project_fabric_topgainer_routing.py`. Avoid changing code during this pilot.

## Offline verification (if dependencies available)

Never trigger `workflow_dispatch` or execute the production scanner for this audit. Use a shell with no production secrets:

```bash
PROJECT_FABRIC_DATABASE_URL='' PROJECT_FABRIC_DB_REQUIRED=false PROJECT_FABRIC_INTEGRATION_TESTS=0 python -m py_compile main.py scanner/*.py project_fabric/*.py storage/*.py bot/*.py
PROJECT_FABRIC_DATABASE_URL='' PROJECT_FABRIC_DB_REQUIRED=false PROJECT_FABRIC_INTEGRATION_TESTS=0 python test_regressions.py
```

If running on Windows PowerShell, set the three environment variables separately for the current session instead of using POSIX inline assignments. Running broader database tests requires an isolated test Postgres and explicit human authorization.

## Report format

- **Observed:** files, functions, actual routing decision, and documented config flags.
- **Risk:** bug hypothesis backed by a reproducible source path.
- **Verification:** exact commands, exit codes, failures; distinguish code inspection from executed tests.
- **Next action:** no code change if routing is already correct; otherwise request a separate scoped patch with a failing offline test.

Do not claim that ECC is installed, that tests passed, or that live Telegram delivery was checked unless there is direct evidence.
