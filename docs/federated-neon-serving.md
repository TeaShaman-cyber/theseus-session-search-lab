# Federated Neon serving spike

Issue #64 evaluates a read-only Session Search serving plane whose request path does not depend on MarcoPolo.

## Authority boundary

```text
Google Drive / accepted source exports = durable source authority
Neon project shards                  = rebuildable derived projections
Session Search router                = deterministic fan-out and merge
Arcade / serving endpoint            = future ChatGPT-facing request plane
MarcoPolo                            = engineering, QA, ingest, rebuild, recovery
```

Raw provider exports are not copied into Neon for this design. A shard stores only normalized/searchable projection state and bounded provenance needed to prove which accepted source generation produced it.

## Shard lifecycle

Each shard has one active generation and generation state is explicit:

```text
BUILDING -> READY -> active pointer
```

Search rejects non-READY generations. The reader role receives no table SELECT privilege and can execute only bounded search/status functions.

## Federation semantics

The router selects only shards matching known provider/session/time routing metadata. Broad searches query all matching READY shards. Per-shard raw relevance scores are not compared across independent indexes; merge uses deterministic reciprocal-rank ordering with stable provenance tie-breaks.

Terminal search states:

- `FOUND` — all selected shards responded and at least one hit exists;
- `NO_SIGNAL` — all selected shards responded and no hit exists;
- `PARTIAL` — at least one relevant shard is unavailable, non-READY, or returns a generation different from the catalog identity.

A failed shard is never silently omitted.

## 2026-10-24 live synthetic canary

Two disposable Neon PostgreSQL 17 projects were provisioned in `aws-us-east-2`:

- `chatgpt-0001` -> `theseus-ss64-chatgpt-a`;
- `hermes-0001` -> `theseus-ss64-hermes-b`.

Both installed the exact versioned migrations under `sql/neon/`, loaded one synthetic session/two messages, reached READY, activated the expected generation, and returned the shared query `copper compass` through the bounded search function.

ACL readback on both shards showed:

- `session_search_reader` can execute the bounded search function;
- `session_search_reader` cannot SELECT `session_search.messages` directly.

Fresh live results from both projects were then passed through the repository `session_search.federation.federated_search` implementation. The merged result was `FOUND`, queried both shards, preserved both generation IDs, and reported no unavailable shards.

Neon automatically exposes provider-managed `cloud_admin -> neondb_owner` administration membership for created roles. That managed edge is distinct from the application reader capability and must not be used as evidence that an end-user serving credential is least privilege.

## Still unverified

This checkpoint does **not** prove the final ChatGPT request path. Pending work under #64:

- deploy/verify the independent serving endpoint/router;
- connect it through Arcade as a compact ChatGPT plugin surface;
- prove an end-to-end query with no MarcoPolo request-time execution;
- exercise an intentionally unavailable live shard through that serving path;
- decide whether the two disposable projects are promoted or deleted.
