---
name: graphify
description: Map repository Python code dependencies and answer questions about architecture, call flow, data boundaries and related modules. Use before cross-module changes to Bot 4; invoke the local Graphify CLI.
---
# Graphify — Bot 4 code graph

Official source: https://github.com/Graphify-Labs/graphify (Apache-2.0). This is a project-specific skill adapter. The Graphify CLI must be installed separately in the development environment.

1. Read `AGENTS.md` and `docs/engineering/BOT4_CODE_MAP.md`.
2. Confirm `graphify` CLI is available (`graphify --help`). On a developer machine: `uv tool install graphifyy` and optionally `graphify install --project --platform agents` to replace this adapter with the full upstream skill and its supporting references.
3. Run `graphify .` from the repository root, or use the explicitly opt-in GitHub Actions workflow `AI Engineering Audit` to generate an isolated artifact. Generated `graphify-out/` is gitignored.
4. Start with `graphify query "where does scanner/market_data.py call DataGateway?"` and `graphify explain "DataGateway"`. Verify inferred edges against source, don't infer production behavior from the graph alone.
5. Never feed .env, credentials, Neon dumps or other production runtime data into the graph. Do not enable paid cloud services or third-party LLM backends.
6. Do not install Graphify into `requirements.txt` or the 4-minute market scanner workflow; do not call ZAPI or Telegram for static analysis.

For bugs, follow `systematic-debugging` and `verification-before-completion`. For changes, follow `ponytail` minimal-diff guidelines.
