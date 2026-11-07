# Everything Claude Code (ECC) pilot — Bot 4

## Purpose and current status

This is a **non-production, opt-in coding-agent experiment**, limited to the Top Gainer Potential private-alert path. Adding these files prepares the project for ECC-assisted review but **does not install or execute ECC**, change Bot 4 signals, or verify live Telegram delivery.

Source: https://github.com/affaan-m/ECC (official MIT-licensed repository). Do not use unofficial installers.

## Isolation

- Trial branch: `experiment/ecc-bot4-offline-audit`.
- Production runtime, schedules, credentials, filters, database, and alert recipients must not be changed.
- This project already has `AGENTS.md` and local debugging/verification skills; preserve their precedence.
- No ZAPI request, Neon production query, live bot execution, or real Telegram message during this pilot.
- Hosted PR checks consume **some GitHub Actions minutes**. Offline execution on a local PC consumes no Actions minutes and no ZAPI quota.

## Setup on your own Windows developer PC (optional; requires user interaction)

1. Open a local clone of `bernadludyy/cuankus-momentum` on this **trial branch**, not `main`.
2. Install Node.js 18+, Git, and Claude Code 2.1+, then check `node --version`, `git --version`, `claude --version`.
3. Review ECC's current documentation, plugin permissions, and hooks. From the repository folder, run `npx ecc-universal@2.2.3 setup`. Choose **project** scope and **standard** hooks only after reviewing the changes; avoid global installation for the initial Bot 4 pilot.
4. In Claude Code, check `/plugin list` shows `ecc@ecc`. This verification **must be done locally**; repository files alone cannot prove installation.
5. Alternatively, for a supported Codex CLI version, use `codex plugin marketplace add affaan-m/ECC` then `codex plugin add ecc@ecc`, and verify with `codex plugin list --json`. Choose **one** harness first.

The pinned release is not a security audit. Do not enter API keys into ECC setup prompts or enable optional network/memory integrations for this test.

## Pilot exercise

Inside the IDE/agent session, read `CLAUDE.md` and use `.agents/skills/bot4-ecc-audit/SKILL.md` as the exercise. If the ECC plugin exposes `/ecc:plan`, ask:

> Audit Bot 4 Top Gainer Potential routing in read-only mode. Verify private recipient only, regular Top Gainer disabled, and filter 2 / PTSN disabled. Produce exact file-path evidence and an offline test plan; do not change source or contact any production service.

Then run `/code-review` if supported, or request a manual review from the coding agent. Test the Python code only with production credentials removed, as stated in `AGENTS.md`. Do **not** run `test_telegram.py`, `test_connection.py`, or a live scanner smoke test.

## Acceptance criteria

- Project-specific ECC instructions and discoverable local skill are present.
- Review identifies real source paths and explains whether potential alerts can reach a group.
- Any test result includes command and fresh output. No test is deemed passing by inference.
- `main` is unchanged and the pilot branch is unmerged.
- Installation/actual invocation of ECC must be verified separately on a user-controlled agent; until then status is **configured for trial, not ECC executed**.

## Rollback

Close the trial PR without merging and optionally delete the trial branch. No production deploy, workflow dispatch, or API-key rotation is required if no credentials were disclosed or used.
