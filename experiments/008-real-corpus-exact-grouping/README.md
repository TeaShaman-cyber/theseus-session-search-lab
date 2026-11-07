# Real-corpus exact-text grouping benchmark (#188)

This experiment measures one change only:

`current recall candidate pool -> current rerank -> exact-body grouping -> Top-K`

It does **not** change FTS tokenization, translation, BM25/FTS rank scores, session
candidate generation, the accepted corpus, or production serving.

## Private inputs

The runner takes two local inputs that are never committed:

- an existing verified/recovered `corpus.sqlite3` projection;
- the frozen #116/#127 gold JSON (`case_id`, language, query text, target content fingerprint).

Public output intentionally contains only case IDs, ranks, aggregate metrics, and
candidate-preservation checks. Query text, target fingerprints, session/message IDs,
and corpus text are not emitted.

## Why candidate budget 30

Current `search_corpus(..., limit=10, recall=True)` uses three rows per session and a
candidate limit of `10 * 3 = 30`. The benchmark fixes that exact pool and reranks all
30 before taking the first 10. It verifies that the baseline first 10 matches current
recall output case-by-case. This isolates exact grouping from candidate-budget changes.

The separate observation that changing requested limit can change the current recall
prefix is real, but it is **not** used as evidence for exact grouping here.

## Run

    PYTHONPATH=. python3 experiments/008-real-corpus-exact-grouping/benchmark.py \
      --corpus /private/path/to/corpus-root \
      --gold /private/path/to/frozen-gold.json \
      --k 10 \
      --candidate-budget 30 \
      --out receipts/188-exact-grouping-real-corpus.public.json

## Accepted 2026-11-07 result

On the recovered 1,253-artifact / 589-session SQLite candidate and the 20-row frozen
RU/EN gold set:

- current recall prefix parity: 20/20;
- average unique bodies in baseline Top-10: 6.0;
- grouped Top-10: 10.0;
- overall R@10: 0.35 -> 0.40;
- overall MRR: 0.1983 -> 0.2280;
- four cases improved, sixteen stayed equal, zero worsened;
- every candidate occurrence remained represented in exactly one exact-text group.

This is evidence that exact duplicates consume useful ranking slots on this corpus.
It is **not** yet a production cutover decision: the serving response contract still
needs an explicit way to expose all branch/event provenance for a selected text group.
