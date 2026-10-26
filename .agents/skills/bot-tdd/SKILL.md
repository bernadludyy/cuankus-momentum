---
name: bot-tdd
description: Test-driven workflow for bot logic, cache handling, scoring, alert routing, and bug fixes. Use when behavior should be implemented or fixed test-first.
---

# Bot TDD

Adapted from `mattpocock/skills` TDD guidance.

1. Name the public behavior seam before writing the test.
2. Write one test that fails for the requested behavior.
3. Confirm the failure is meaningful, not an environment or fixture error.
4. Implement only enough code to make that test pass.
5. Repeat in vertical slices.
6. Run the narrow test first, then the relevant offline regression suite.

Tests must verify behavior through public interfaces. Avoid testing private helpers, duplicating the implementation in assertions, or mocking the boundary that actually needs verification. Production network and messaging side effects must remain disabled.
