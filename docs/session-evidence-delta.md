# Bounded session evidence delta — reference experiment

Related to #340, #338 and Dreaming Worker #65.

Capture a single explicitly selected projected session under the same corpus
mutation lock as the accepted membership receipt. Reuse projection consistency
verification. This is a correctness reference with explicit event and
source-link limits; it does not yet optimize the corpus-level verification
scan or discover the affected session closure.

The event key is the projected session ID plus existing local identity. A
version digest covers canonical message identity, role/class/content type,
text, provider message ID, observed time and provider order. Text is hashed
in-process, not returned. Absolute ordinal is excluded: inserting an earlier
event does not itself change every subsequent event's standalone identity.
An analysis using surrounding context/order must separately bind those
dependencies. The accepted set and source links are stored separately from
the event version. Scope and event IDs are sensitive metadata; keep receipts
within trusted custody. They are not authentication or read authorization.

Compare two validated receipts for the same scope and session. Return added,
removed, changed and provenance-only event identities separately, plus a
coverage-change flag. A repeated capture of unchanged content changes source
links, not the event version. Equal text in distinct events remains distinct.
Removed events (including potential branch rerouting), lost provenance and
coverage changes require reconciliation; do not silently discard prior
knowledge or infer no work. Provenance-only is a classification, not automatic
permission to skip analysis that depends on provenance. Global membership
change alone does not invalidate unchanged event fingerprints.

The receipt binds exact observed source artifact hashes, page positions and
source-object hashes to each projected event. It describes a projection,
not the raw source bytes or a verified complete history. Currentness is always
UNKNOWN_WITHOUT_SOURCE_WATERMARK. No evidence body transport, source cursor
advance, generation promotion, automatic inference or source authority change.
Limits bound returned row counts, not total query I/O or bytes; performance
and affected-session selection remain separate experiments.

Tests exercise actual synthetic ingest, recapture, distinct equal-text
events, unrelated sessions, time and coverage changes, rebuild equivalence,
bounds and corrupt receipts. A synthetic removal receipt tests classification,
not a demonstrated live rerouting case.

## Shared SQLite / federated Neon projection contract

The current Neon v1 materializer transfers session ID, ordinal, materialized
message ID (provider ID or existing anonymous local identity), role, search
class, create time and text. It does not transfer the full canonical message
digest, content type, provider order, coverage reason or artifact/page/source
object links. Full source provenance therefore remains the separate
accepted-corpus receipt above. Do not reconstruct it from a Neon search or
browse hit.

`session_search.projected_evidence` is the common bounded comparison layer.
Its SQLite adapter reads the materializable fields under the existing corpus
lock and projection check. Its Neon adapter consumes complete, explicitly
generation-pinned `session_browse` pages from the existing router contract;
it requires the matching provider/shard/generation/session, pinning witness,
queried shard and no unavailable shards, and validates positions, offsets,
Unicode character lengths, coverage and bounded exhaustion. No ACTIVE fallback
or lexical query. Both use one event normalization, fingerprint and comparison
implementation, independent of backend and generation. The caller provides
a stable trusted corpus scope; scope/provider/session must match. Standalone
event fingerprints exclude absolute ordinal; order changes are reported
separately for contextual analysis. Coverage and backend lineage are retained.
UTF-8 byte, event and page limits fail closed; they bound returned data rather
than total scan I/O. The common receipt is metadata only and weaker than the
source receipt. A matching common fingerprint alone does not authorize
skipping source/provenance-dependent work.

The existing bundle format and database schema are unchanged. A disposable
PostgreSQL 16 acceptance job ingests synthetic captures with the actual Python
runtime, builds and verifies canonical shard bundles for two generations, loads
those rows using the existing restricted BUILDING capability and marks READY
with the existing marker. The test uses actual shard and router JS handlers,
including their MCP session_browse path, with a psql test transport to the
real SQL function. It compares both snapshots and deltas against SQLite,
including equal-text distinct events, anonymous identity, Unicode pagination,
time change and incomplete coverage. It rejects BUILDING reads and direct
reader table access. The test transport is not an Arcade/Neon cloud deployment
or the actual Arcade loader client. No new route or permission is introduced.

Remaining integration: a trusted source-to-generation binding for full
provenance, affected-session selection across shard movement/branch changes,
and bounded Dreaming Worker admission with explicit custody and dependency
checks. This common projection reader is a library, not an unattended feed.
