---
name: bot-output-quality
description: Anti-slop quality gate for trading-bot comments, Telegram command responses, alerts, and user-facing documentation. Use whenever an agent creates or edits those outputs.
---

# Bot Output Quality

Curated for the `bernadludyy` bots from the code and copywriting guidance in `miqdadbadjuber/anti-slop`. This is a development-time review skill, not a runtime package.

## Scope

Apply this skill to:

- Python and JavaScript comments or docstrings;
- Telegram alerts and command responses;
- README files, setup instructions, release notes, and operator messages.

Do not apply UI, mobile-layout, animation, or design-system rules unless the project actually contains a user interface.

## Source-truth gate

- Never invent a price, percentage, rank, broker, date, signal, confidence value, citation, or test result.
- Preserve the data date and state whether values are live, delayed, cached EOD, or unavailable.
- Keep units explicit: shares, trades, rupiah, million, billion, percent, or price level.
- Separate observed data from interpretation and from a suggested action.
- If data is stale, partial, or missing, say so directly. Do not turn missing evidence into a bullish or bearish narrative.

## Telegram output gate

- Lead with the ticker, setup, and decision-relevant facts.
- Prefer short Indonesian sentences and the terms already used by the project.
- Avoid hype, fake urgency, generic praise, decorative emoji bullets, repeated conclusions, and promises of profit.
- Do not hide risk behind vague language. Name invalidation, data limitations, and relevant timeframe when the command provides them.
- Keep private/group routing labels and live/EOD labels unchanged unless the requested behavior explicitly changes them.

## Code comment gate

Keep comments that explain why a decision exists, including market-session rules, data-source limits, safety constraints, and non-obvious calculations.

Remove or reject comments that:

- restate the next line of code;
- act as decorative section banners;
- narrate obvious control flow;
- preserve a changelog inside the source file;
- leave dead code commented out;
- claim a fix, optimization, or safety property without evidence.

## Delivery check

Before completion, report pass or fail for:

1. factual grounding and date freshness;
2. live versus EOD labeling;
3. units and financial terminology;
4. concise Telegram copy;
5. useful code comments only;
6. no change to runtime, API calls, database writes, schedules, or recipients unless requested.

If any item fails, fix it or state the blocker. Do not mark the output ready while a fabricated value or ambiguous data date remains.
