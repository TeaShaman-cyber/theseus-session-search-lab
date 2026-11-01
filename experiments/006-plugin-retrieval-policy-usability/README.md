# Experiment 006  plugin retrieval-policy usability benchmark

This frozen public-safe benchmark supports Session Search issue #137. It compares the existing local CLI lexical behavior with the plugin-facing federated MCP router after adding explicit `strict | recall | auto` retrieval policy.

## What is measured

The same synthetic corpus is searched by the real local CLI implementation (`session_search.search.search_corpus`) in strict and recall modes. Those exact rows are then presented as one healthy shard to the real MCP router. The benchmark checks that:

- plugin `strict` preserves CLI strict results;
- plugin `recall` preserves CLI recall results;
- plugin `auto` performs only bounded `strict -> recall` escalation after a clean strict `NO_SIGNAL`;
- the retrieval receipt reports requested/effective mode and counts truthfully;
- a clean strict+recall miss stays `NO_SIGNAL`;
- the synthetic shard remains fully available.

The four frozen usability classes are:

1. precise lexical lookup;
2. over-specified natural-language query;
3. concept split across messages in one session;
4. clean miss / UNKNOWN discipline.

## Authority boundary

This benchmark proves **policy/interface parity**, not live serving equivalence.

```text
real local CLI search on synthetic corpus
        -> retrieval semantics witness
same rows through real MCP router
        -> plugin-policy witness
live Neon search_v2 + deployed shard/router + ChatGPT App
        -> separate candidate/App-backed smoke
```

It therefore does not establish production deployment, live shard SQL parity, App installation freshness, or semantic/reconciliation capability. Those remain separate acceptance steps under issue #137.

## Frozen receipt

`baseline.public.json` is generated from `benchmark.py` and is committed only when all four cases pass. It contains only synthetic/public-safe identifiers and policy metadata.

Run:

```bash
python3 experiments/006-plugin-retrieval-policy-usability/benchmark.py
python3 -m unittest -v tests.test_plugin_retrieval_policy_benchmark
```
