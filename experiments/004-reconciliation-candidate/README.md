# Experiment 004 — minimal authority/reconciliation candidate

This experiment compares one deliberately small library-style reconciliation layer against the frozen baseline from Experiment 003 / issue #50.

It does not change production Session Search retrieval. It adds no runtime dependency, vector store, embedding model, RDF stack, ontology server, or persisted derived state.

## Candidate

A local controlled-concept registry provides:

- one preferred label;
- zero or more aliases;
- exact normalized form matching only.

The resolver applies three authority rules:

1. one matched concept may expand to its other controlled forms, but any resulting hit remains `CANDIDATE_ONLY`;
2. two or more concept matches return `AMBIGUOUS_CONCEPT` and no semantic winner is chosen;
3. registry knowledge cannot fabricate corpus evidence — an alias expansion with no corpus hit remains `UNKNOWN`.

Strict lexical hits and recall-only hits are typed per hit. A mixed response therefore cannot accidentally promote its recall candidates merely because another row matched strict lexical search.

## Authority boundary

```text
accepted artifact/message evidence
        = historical evidence authority

strict lexical hit
        = lexical evidence only

recall / controlled alias expansion
        = discovery candidate only

ambiguous controlled concepts
        = AMBIGUOUS; no automatic choice
```

Every returned hit remains `NON_AUTHORITATIVE_RETRIEVAL`. `LEXICAL_EVIDENCE_ELIGIBLE` means only that the exact historical message is eligible to be inspected as lexical evidence; it does not promote a derived concept interpretation into historical fact.

## Frozen comparison result

`comparison.public.json` binds the exact Experiment 003 baseline receipt and the controlled-concept registry by SHA-256.

Observed on the seven frozen classes:

- current recall behavior is preserved for adjacent wording and split-across-message cases;
- broad contextual recall remains `CANDIDATE_ONLY`;
- the alias-only gap is recovered through controlled `RAG` expansion without changing authority;
- exact lexical rank-1 behavior is preserved;
- message provenance remains resolvable;
- `Mercury` becomes explicit `AMBIGUOUS_CONCEPT` with both candidate sessions preserved;
- stale registry aliases do not create synthetic hits.

Current research signal: `KEEP_MINIMAL_LOCAL_RECONCILIATION`.

This result earns the smallest local alias/ambiguity layer for consideration. It does **not** establish a need for RDFLib, SKOS tooling, embeddings, external vocabularies, or a semantic reranker.

## Run

```bash
python3 experiments/004-reconciliation-candidate/candidate.py
```
