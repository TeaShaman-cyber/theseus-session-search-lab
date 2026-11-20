# QA and verification

Session Search has one canonical repository-development gate:

```bash
./tools/dev/check
```

The gate composes existing checks rather than defining a second verification system.

## Verification layers

| Layer | Existing mechanism | Claim established |
| --- | --- | --- |
| Public repository contract / privacy | `python3 scripts/verify_repo.py` | Required public files and sanitized receipts exist; forbidden private/binary corpus artifacts and obvious secret patterns are not tracked. |
| Unit and regression behavior | `python3 -m unittest discover -s tests -p 'test_*.py'` | Artifact normalization, corpus ingest, provider adapters, retrieval, reconciliation, rebuild, and known regressions behave as encoded by tests. |
| Python parse/import sanity | `python3 -m compileall -q session_search scripts tests` | Tracked Python sources compile syntactically. |
| Patch hygiene | `git diff --check` | Current tracked/staged patch has no whitespace errors. |
| Live corpus authority/integrity | `python3 -m session_search.corpus verify --corpus <path>` | Accepted ledger and immutable artifacts are valid and the current SQLite/FTS projection derives from accepted artifact evidence. |
| Corpus evidence watermark | `python3 -m session_search.corpus status --corpus <path>` | Reports observed accepted/message watermarks and coverage/source counts; does **not** prove live-provider freshness. |
| Projection repair / equivalence | `python3 -m session_search.corpus rebuild --corpus <path>` | A fresh disposable projection can be regenerated from accepted artifacts and atomically replace the old projection only after verification. |

The live corpus commands operate on private evidence and therefore are not part of public CI. Public CI exercises the same contracts with synthetic fixtures.

## Deployment/promotion claim boundary

A runtime promotion can be implemented by switching an external stable binding (for example `SESSION_SEARCH_CORPUS`, an explicit deployment path, or a symlink) from one versioned corpus candidate to another. The binding itself is not corpus authority and is not part of the repository's hidden defaults.

Readback after promotion should establish only the concrete deployment postconditions being claimed, such as the resolved target plus a bounded projection identity/count check. Those observations do **not** imply `VERIFIED`. The full `corpus verify` path is the mechanism that binds the current projection back to accepted ledger membership and immutable artifact bytes. If that stronger check is not run, is resource-blocked, or fails for an execution reason, report the promotion and verification states separately rather than collapsing them into one success label.

## Derived-state rule

`corpus.sqlite3` and FTS are disposable projections, never authority. A projection is not `VERIFIED` merely because SQLite integrity, row counts, and ledger metadata agree internally. Verification must bind the searchable semantic/provenance state back to the accepted ledger plus immutable artifact bytes.

Branch routing is derived state under the same rule. Projection schema `session-search-corpus-v2` stores one versioned artifact route per accepted artifact, preserving the accepted ledger `session_id` separately from the projected branch session. `verify` checks route membership/version and rebuild derivability; an old v1 projection reports reconciliation required until `rebuild` regenerates v2 from immutable accepted evidence.

This rule is regression-covered in `tests/test_corpus_store.py` and tracked by Issues #28 and #43.


## Federated serving implementation boundary

The supported federated serving implementation is the JavaScript router under `functions/router/`. The earlier Python helper `session_search/federation.py` was used during the synthetic #64 spike but had no live non-test consumer and was removed under #72 rather than maintained as a second parallel callback contract.

Do not reintroduce a separate Python federation implementation merely for symmetry. If a Python consumer is needed later, define one shared contract against the deployed router semantics instead of forking routing/filter behavior again. Historical #64 documentation that records the earlier canary remains valid as point-in-time evidence.

## Risk-based Codex review

Codex review is not a universal promotion gate. Ordinary low-risk PRs remain advisory and may be promoted while Codex is pending when the normal promotion conditions are otherwise satisfied. For protected verification/authority/permission/promotion/canonical-source/lifecycle/fail-closed boundaries, however, the repository now has a blocking exact-head workflow gate. Hosted QA, repository verification, review evidence, risk, scope, and the maintainer promotion decision remain separate claims.

For PRs that change verification, authority, permission, promotion, canonical-source, lifecycle, or another fail-closed safety boundary, use a bounded exact-head wait when Codex review is pending or stale after PR creation. `tools/pr/codex-review-wait.sh <pr-number> <expected-head> [timeout-seconds] [poll-seconds]` pins the promotion candidate explicitly and defaults to 120 seconds with a 10-second poll; the repository `review-gate` workflow currently supplies a 600-second bound with the same 10-second poll for protected PRs. If GitHub still reports an older PR head after a push, the helper reports `PENDING / pr-head-not-current` rather than accepting review evidence for the stale head. `CLEAN` and `FINDINGS` are both terminal evidence; the helper itself does not decide merge eligibility, while the protected workflow accepts only exact-head `CLEAN`. `PENDING` and `STALE` wait only to the configured bound and then return an explicit `TIMEOUT` disposition rather than holding the PR open indefinitely. The remaining deadline also bounds each in-flight status request; a slow GitHub request returns `TIMEOUT / status-request-timeout` instead of overrunning the advertised wait bound.

`FINDINGS` on the exact head must be explicitly reconciled before routine promotion: fix a confirmed in-scope defect, reject it with evidence, or move a valid out-of-scope concern to a narrow follow-up issue. A bounded timeout is visible evidence, not proof of cleanliness; if promotion still proceeds, any late finding must receive the same explicit reconciliation.

Use `tools/pr/codex-review-status.sh <pr-number>` for a one-shot exact-head advisory state as `PENDING`, `FINDINGS`, `CLEAN`, or `STALE`. Both status and wait helpers report evidence only and deliberately do not enforce merge eligibility.

### Risk-based workflow gate

`.github/workflows/review-gate.yml` turns the protected-boundary part of this policy into a repository check without making Codex universal merge authority. `scripts/review_gate_required.py` classifies the changed paths. Verification, review machinery, GitHub workflows, serving auth/router boundaries, corpus authority/reconciliation, and promotion/materialization paths require the exact-head Codex wait; ordinary product, test, and documentation changes do not by default. The classifier is intentionally conservative and path-based rather than pretending to infer semantic risk from a diff.

For protected changes, the workflow runs the existing `codex-review-wait.sh` against the PR head and accepts only terminal `CLEAN`. Exact-head `FINDINGS`, timeout, `PENDING`, `STALE`, an older-head receipt, or an unexpected state fail the workflow. The review job checks out the base branch before invoking the helper so a PR cannot weaken the trusted review parser/wait logic that judges that same PR.

### PostHog longitudinal review readback

After exact-head Codex review and before the final readiness conclusion for a relevant Session Search PR, the main agent should separately read PostHog project `649512`, GitHub warehouse source prefix `sessionsearch`. Confirm source sync/freshness, then inspect comparable workflow/job failures and durations over a bounded recent window (normally 14 days). When review timing is relevant, also inspect whether comparable PRs merged before Codex reached its final review state. Significant suspicions must be corroborated against current GitHub Actions and the exact PR head before they are treated as actionable.

Record the result next to the review conclusion in this compact form:

```text
POSTHOG_CI_HISTORY: repo=theseus-session-search-lab head=<40-char-sha> status=VERIFIED|STALE|NO_SIGNAL|UNAVAILABLE sync=<UTC|UNKNOWN> signal=<brief finding> evidence=<query/link|NONE>
```

PostHog is derived longitudinal evidence and **not a merge authority**. `NO_SIGNAL` means no relevant evidence was observed, not that no problem exists. `STALE` or `UNAVAILABLE` must remain visible but do not become an implicit PASS or a new hard merge dependency. Do not move ChatGPT/PostHog OAuth into GitHub Actions merely to automate this readback.

## Adding QA

Prefer extending an existing layer before adding another checker:

1. product behavior or a reproduced bug -> add a focused test under `tests/`;
2. public repository/privacy invariant -> extend `scripts/verify_repo.py`;
3. generic mechanical pre-review check -> add it to `tools/dev/check`;
4. private/live-corpus acceptance -> use `session_search.corpus verify` / `rebuild` and record only sanitized receipts or conclusions publicly.

An unrun or uncovered check is `UNKNOWN`, never PASS. Search results, SQLite state, CI status, and receipt existence do not independently grant authority beyond the claim each layer verifies.

## Currentness boundary

Session coverage, corpus freshness, and live-provider completeness are different claims. A `COMPLETE_EXPOSED_CONVERSATION` session can still live inside a corpus whose newest accepted capture is old. Conversely, a recently accepted partial capture does not prove complete history.

Without an authoritative acquisition/source watermark, `corpus status` must report `UNKNOWN_WITHOUT_SOURCE_WATERMARK`; wall-clock age alone is not enough to label an archive `CURRENT` or `STALE`.
