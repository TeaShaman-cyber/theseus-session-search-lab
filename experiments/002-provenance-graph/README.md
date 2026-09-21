# Experiment 002 — provenance-preserving graph projection

This experiment tests one question only: can a semantic graph remain a **regeneratable, non-authoritative projection** while every extracted edge retains a machine-checkable path back to Session Search evidence?

It deliberately performs no LLM extraction, entity-resolution model, inference generation, visualization, or graph-database work. Claims are supplied as JSONL so the experiment isolates the provenance/authority seam.

## Authority model

```text
accepted immutable artifact
  -> payload page / source object
  -> normalized message + canonical_message_sha256
  -> exact cited evidence span
  -> extracted graph edge (derived interpretation)
  -> inferred edge (derived from projected edges)
```

The authoritative historical statement is that a verified source message contained the cited evidence span. Neither an extracted SPO edge nor an inferred relationship becomes historical truth.

## Run

```bash
python3 experiments/002-provenance-graph/projector.py \
  --corpus /path/to/corpus \
  --claims claims.jsonl \
  --output graph.json
```

An extracted JSONL record binds `session_id`, identified `message_id`, exact `canonical_message_sha256`, and an exact text span. The projector resolves that message through `message_sources` to accepted immutable artifact SHA(s) and source-object SHA(s). A mismatch fails closed.

An inferred record requires `method` plus `source_claim_ids`. Its serialized edge is explicitly `INFERRED_RELATION / NON_AUTHORITATIVE_PROJECTION` and contains resolved `source_edge_ids`.

The projector opens the corpus read-only after `verify_corpus()` returns `VERIFIED`; it does not mutate Session Search state.
