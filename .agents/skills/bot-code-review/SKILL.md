---
name: bot-code-review
description: Review a bot change against repository standards and the requested behavior as separate axes. Use for branches and pull requests before merge.
---

# Bot Code Review

Adapted from `mattpocock/skills` two-axis code review.

## Standards axis

Check the diff against `AGENTS.md`, project documentation, security boundaries, API/cache strategy, test conventions, and schedule constraints. Flag hard violations separately from maintainability concerns.

## Specification axis

Check whether every requested behavior is implemented, whether anything unrequested was added, and whether outputs, recipients, dates, units, or ranking semantics differ from the requirement.

## Bot risk overlay

Always inspect:

- duplicate or missing alerts;
- private versus group routing;
- stale EOD/intraday mixing;
- extra API calls or Actions minutes;
- unsafe database migrations;
- missing market-hours guards;
- silent fallback to stale or partial data.

Report findings by severity with file and line evidence. Keep the two axes separate so clean code cannot hide the wrong behavior.
