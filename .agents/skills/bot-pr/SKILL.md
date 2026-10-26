---
name: bot-pr
description: Write concise pull requests for bot changes with verification evidence, rollback, usage impact, and blast radius.
---

# Bot PR

Adapted from `mattpocock/skills` PR guidance.

Use this structure:

```markdown
## Summary
<smallest diagram, diff sketch, or file tree that explains the change>

## Evidence
- Before: <failing test or observed symptom>
- After: <passing test and relevant regression result>

## Runtime impact
- ZAPI/API calls: unchanged / explain
- GitHub Actions minutes: unchanged / explain
- Neon schema or writes: unchanged / explain
- Telegram recipients: unchanged / explain

## Merge danger
- Door: one-way / two-way
- Blast radius: <scope>
- Rollback: <exact rollback method>
```

Keep it factual. Never claim production verification when only offline tests were run.
