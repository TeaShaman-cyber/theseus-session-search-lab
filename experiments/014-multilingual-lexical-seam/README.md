# Backend-neutral multilingual lexical seam (#188)

## Question

The independent #203 holdout showed that increasing the local per-session cap from
3 to 7 did not recover any additional target. This experiment asks the next causal
question without choosing a storage/search vendor:

Are the misses primarily caused by language normalization, by natural-language
query planning, or by the backend/ranker after both are improved?

The experiment is deliberately local and disposable. It does not change serving,
Neon, SQLite authority, or any serverless deployment.

## Boundary

The same frozen 10-case historical-query holdout from #203 is reused here only as
development/diagnostic evidence. It is no longer an independent promotion set
for this new hypothesis. Any production choice derived from this work requires a
new independent validation set.

The project-owned seam under test is:

raw text/query -> shared lexical analyzer -> query planner -> backend adapter -> Candidate v1

No SQL syntax belongs to this seam.

## Analyzer arms

All arms index the same 177,633 dialogue + evidence messages and use the same
BM25 candidate shape: per-session cap 3, global candidate budget 30.

- surface: Unicode NFKC + casefold + word tokens.
- snowball: Russian Snowball stemmer + English Snowball stemmer.
- lemma: pymorphy3 Russian normal forms + English Snowball stemmer.

Runtime versions for the measured run:
- snowballstemmer 3.1.1
- pymorphy3 2.0.6
- pymorphy3-dicts-ru 2.4.417150.4580142

Analyzer dependencies are experiment-only and lazy-loaded; they are not production
dependencies.

## Measured candidate recall

Natural query, cap=3, budget=30:

| Arm | target reachable / 10 |
| --- | ---: |
| current/surface | 1 |
| Snowball RU+EN | 2 |
| lemma RU + English stem | 2 |

Language normalization changes individual outcomes but does not explain the main
8-9 misses by itself.

### Where the current surface path loses targets

All 10 targets match at least one raw query token. The loss happens later:

- 10/10 targets have a lexical OR match;
- only 3/10 are within the best three BM25 rows of their own session;
- only 1/10 remains inside the global post-cap Top-30 candidate pool.

Natural OR queries are extremely broad on this holdout: individual queries can
match tens of thousands to well over one hundred thousand messages.

## Query-planning diagnostics

A target-independent diagnostic kept only the corpus-rarest query terms. It did
not fix the surface path (max 1/10 reachable). With lemma normalization it reached
at most 3/10. Therefore simple stopword/frequency pruning is insufficient.

A separate oracle upper bound used only query terms known to also occur in the
correct target. This is intentionally impossible in production and is used only to
measure headroom:

| Representation | candidate reachable | BM25 Top-10 |
| --- | ---: | ---: |
| surface oracle | 4/10 | 1/10 |
| lemma oracle | 8/10 | 7/10 |

For the lemma-oracle query, changing per-session cap from 3 to 7 to 30 left the
result unchanged at 8/10 reachable and 7/10 Top-10.

## Interpretation

The evidence supports an interaction:

1. FACT: morphology/normalization can materially improve individual lexical
   matches and ranks.
2. FACT: passing the whole natural-language recall request into OR search
   produces an enormous noisy match set.
3. FACT: correct term selection plus lemma normalization has much more headroom
   than either morphology alone or rare-term pruning alone.
4. FACT: the remaining two oracle misses are not repaired by relaxing the
   per-session cap through 30.
5. INFERENCE: the next project-owned abstraction should separate deterministic
   lexical analysis and query planning from backend execution.
6. UNKNOWN: which non-oracle query planner can recover most of the oracle
   headroom without regressions on an independent set.
7. UNKNOWN: whether the final production backend should be SQLite/libSQL,
   Tantivy-derived search, Redis Search, Postgres, or another engine.

Do not select an analyzer, query-term count, backend, or serving cutover from this
10-case diagnostic set.

Public aggregate evidence is in
receipts/188-multilingual-lexical-seam.public.json.
