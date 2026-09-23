# Experiment 003 — authority/reconciliation retrieval benchmark

This is the frozen baseline for Session Search issue #50. It measures the current strict lexical and opt-in recall behavior before any reconciliation, entity-resolution, embedding, or semantic-ranking dependency is introduced.

The benchmark uses only synthetic/public-safe session artifacts. It deliberately includes cases where the current system should succeed, cases where recall should return only a candidate, and gaps that a future reconciliation layer would need to earn the right to address.

## Authority boundary

```text
accepted synthetic artifacts/messages = historical evidence authority
strict/recall ranking                = candidate retrieval projection
future reconciliation                = candidate/review projection only
```

A benchmark hit never upgrades a derived concept interpretation into historical fact.

## Frozen classes

1. adjacent-wording lexical miss recovered by recall;
2. concept split across messages in one session;
3. broad contextual OR candidate that must not be promoted to target evidence;
4. alias/canonicalization gap (`RAG` vs `retrieval augmented generation`);
5. exact lexical positive control;
6. provenance readback through `message_sources` to accepted artifacts;
7. unresolved ambiguity (`Mercury`) where multiple concepts must remain visible.

## Run

```bash
python3 experiments/003-authority-retrieval-benchmark/benchmark.py
```

Expected v0 disposition is not “all retrieval problems solved”. A PASS means the frozen benchmark observed the preregistered **baseline behavior**, including intentional gaps. The comparison phase may later add one thin reconciliation candidate and compare it against this unchanged baseline.

## Frozen v0 baseline

The committed `baseline.public.json` records the preregistered current behavior:

- adjacent wording: strict misses the intended session; recall surfaces it;
- split-across-messages: strict misses; session-level recall ranks the intended session first;
- broad contextual query: strict returns no result; recall exposes contextual material that must remain candidate-only;
- alias-only query: strict and recall both miss, creating a clean reconciliation opportunity;
- exact lexical positive: strict ranks the intended session first;
- retrieved messages remain resolvable through `message_sources` to accepted artifact provenance;
- ambiguous `Mercury` preserves multiple candidate sessions rather than yielding a unique semantic resolution.

This receipt is a baseline, not an implementation target. A future candidate layer must be compared against it without changing these fixtures opportunistically.
