---
name: bot-diagnose
description: Evidence-first diagnosis for bot failures, stale data, missing alerts, duplicates, and performance regressions. Use when a bot is broken or behaving unexpectedly.
---

# Bot Diagnose

Adapted from `mattpocock/skills` diagnosing-bugs.

1. Redact secrets from every log and command.
2. Build a fast pass/fail loop that reproduces the exact symptom without live Telegram, production Neon writes, or new paid API calls.
3. Minimize the case until every remaining input is necessary.
4. Rank 3–5 falsifiable hypotheses.
5. Instrument one boundary at a time; tag temporary logs so they can be removed.
6. Add a regression test at the real behavior seam before the fix when possible.
7. Apply the smallest fix, rerun the original reproduction and relevant regression suite, then remove instrumentation.

Do not claim a root cause from code reading alone. If no reproducible loop can be built, state the missing evidence and stop at diagnosis.
