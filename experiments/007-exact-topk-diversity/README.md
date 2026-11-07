# Exact-text grouping before Top-K — synthetic baseline (#188)

Purpose: characterize whether repeated **event rows** can consume Top-K slots
without changing the index, stored history, translation or rank scoring.
This is an algorithmic comparison, **not** a production implementation.

Run: `PYTHONPATH=. python3 -m unittest discover -s tests -p test_exact_topk_diversity.py`

Fixture: five branch/session occurrences, three copies of `alpha` at score 9,
one distinct relevant `beta` at score 8 and one nonrelevant `gamma` at score 7.
The event ID `x` is deliberately reused with a different body in another branch.

For K=3, event-level baseline has **1 unique text** and **1 unique relevant
text**. Exact-text grouping has **3 unique texts** and **2 unique relevant
texts**, with original event membership on each selected group. This is a
demonstration of a mechanism on an intentionally crafted fixture, not R@K on
a gold real-query set. The test asserts deterministic ties and strict
exact equality (not hash-only or semantic grouping).

The mechanism does **not** preserve the original event-level Top-K list, all
event rows' visibility in only K selected groups, BM25/ts_rank_cd scores under
a changed index, or session-filter semantics without a corresponding scoped
grouping policy. Gold relevance must be consistent per exact text in this
simple synthetic model. The accepted corpus is unchanged.

Next gate: use existing frozen retrieval queries and explicit gold evidence,
measure unique-relevant recall, duplicate occupancy, branch provenance and
scope/coverage separately. MMR, schema migration and physical intern tables
remain deferred.
