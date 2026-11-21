# Code Review Context for Session Search Functions

When reviewing changes under `functions/`, use persisted SpecD links as repository context when they apply.

If a changed file or named symbol appears in `specs/**/spec-lock.json` under `implementation`:

1. Read the corresponding `spec.md` and `verify.md` before judging semantic correctness.
2. Treat those artifacts as the intended behavior and verification contract for that linked implementation.
3. Do not treat the existence of the link, a passing graph/index job, or a passing test as proof that the implementation satisfies the full human requirement.
4. If the link is missing, stale, ambiguous, or does not cover the changed behavior, say that the SpecD context is insufficient instead of inferring compliance.
5. Keep deterministic CI/test evidence and runtime readback separate from semantic conformance.

Do not duplicate the linked requirements here; the canonical requirement text remains in the SpecD artifacts.
