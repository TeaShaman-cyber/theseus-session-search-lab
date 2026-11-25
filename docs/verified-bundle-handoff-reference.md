# Verified bundle handoff reference (#59, #3)

This experiment connects existing pieces, not a new serving or storage
architecture. The importer reports changed conversations (#343) and
rederives a lost report from a retained accepted-membership receipt (#344).
The materializer already captures accepted membership and assigned rows
under the corpus lock. The bundle contains the exact sessions.csv and
messages.csv used for loading. The verified manifest binds those rows to the
accepted set and generation. The promotion readiness contract checks
source/bundle/READY evidence but does not itself certify live cutover.

The test-only placement_reference matches actual verified bundles in trusted
fixture custody against a simulated complete provider catalog. It reuses
verify_bundle and reads existing sessions.csv; no search, later allocation,
or new persisted index is involved. Two official export scenarios use
actual cumulative ingestion, canonical recovery, deterministic bundles and
the actual allocator with a reduced fixture target to exercise multiple
shards cheaply.

Results:
- After a branch change and unrelated addition, recovery yields the same
  added/changed/removed content IDs as the original import. Current bundles
  exactly cover the source projection. Old, mixed and incomplete catalog
  vectors, and a bundle from a different accepted set, cannot admit the
  new batch. After matching catalog readback, each selected current
  conversation has an exact shard/generation. Prior inventory preserves
  removed conversation identity. Stable conversations are not selected
  merely because their generation changes.
- A repeat export changes accepted-set and bundle generation identity but
  produces an empty content-delta, both at import and after recovery.
  This is not proof that no provenance-sensitive task or pending analysis
  exists.

Proposed minimal handoff: the refresh executor retains the exact verified
bundle custody, source receipt and expected provider vector until the
existing router cutover is independently read back. The worker retains its
last completed accepted membership and analysis version. Recovery compares
that checkpoint with the current accepted source, then binds selected
session IDs to exact promoted bundle rows. No second catalog. If the
catalog changes again, preserve completed work and reconcile unresolved
work against a new verified batch; never silently substitute generations.
A source or generation transition is not a consumer acknowledgement.

Limitations: the test helper is not a production API, proof of live
promotion or durable delivery. It assumes immutable files and a trusted
complete simulated catalog, reads the verified ZIP a second time for
session IDs, and does not implement atomic publication, production
receipt authentication, provider-scope binding, full raw message provenance
or worker completion. Actual per-provider inventory must include every
eligible source session or explicitly classify gaps. Reuse existing
materializer/bundle verification, preferably return private selected
session placement in the same verified operation if later needed; never
add private IDs to the public bundle summary. Before implementation, inspect
the existing refresh custody and Dreaming ledger checkpoint contracts.
Do not infer historical equivalence across analysis/normalization changes.

## Private recovery candidate

`session_search.refresh_handoff_candidate.prepare_recovery_handoff`
now composes the established recovery and bundle checks. It consumes a
trusted rederived delta and previous membership, existing promotion
readiness receipt, independently observed complete current catalog,
exact bundle paths in immutable custody, and the independent
`verify_bundle_session_selection` library function. The latter is passed
as a callable, not invoked through a new service, tool or MCP route.

The first implementation intentionally handles a single provider-exclusive
corpus scope and full replacement of its serving shards. It rejects
unchanged shards of that provider, mismatched or duplicate catalog
entries, wrong external bundle digests and accepted-set/manifest/count
mismatches. Every added/changed ID must occur exactly once; removed IDs
must not occur. It preserves both full membership receipts, current
catalog vector, placement and removal list, plus a deterministic candidate
digest. No message bodies, local paths or credentials are emitted.

This is a candidate under trusted caller custody, not authenticated
delivery or proof of live source completeness. In particular, the
caller must independently establish the provider-exclusive scope,
authenticity/completeness of the input delta, readiness receipt and
catalog observation; their digests are not signatures. A readiness
receipt alone is insufficient. Unselected source sessions have not been
independently enumerated by this function. The existing recovery is a
rare full rederivation under current code; this is not the normal
incremental refresh path or proof of equivalence to an earlier executable.
Upstream currentness remains unknown. A missing/removed conversation
requires explicit consumer disposition; an empty selection does not
acknowledge work. No publication, persistence, or checkpoint advance
occurs. Cross-provider and partial replacement remain unsupported until
their exact source-to-serving equivalence is established.

## Scoped source evidence metadata (recovery only)

`capture_source_evidence_index` reuses the canonical materializer snapshot
and corpus lock, including full derivation verification. It matches the
complete trusted membership receipt (including ledger entry digests) and
exact requested generation, then returns selected searchable message
identities, text SHA-256, canonical normalized message SHA-256, and existing
artifact/member/position/source-object links. Message rows used for the
hashes are the same rows used to derive the serving manifest, not a new
conversation reader. All selected source metadata is private. No message
body is emitted. Titles, IDs and source-object references are still private
metadata, not public receipts. The links identify accepted normalized
artifacts; they are not a claim to retain the original provider ZIP or
that every linked object has identical content (linked projection promotion
may retain earlier observations).

Each session content digest includes its title, coverage state and ordered
searchable message metadata, excluding the source links and generation.
It is an explicit conservative candidate for content-dependent tasks,
not a general dependency identity: provenance-sensitive analysis, hidden
rows and analysis-version changes need separate treatment. The digest
uses the existing Python canonical JSON serialization (including numeric
timestamps); do not assume JS serialization matches for all numbers.
The consumer must bind the actual text received through the existing
reader to the expected message hashes and metadata, enforce bounded
complete paging, and preserve the exact generation and source links
separately from the stable work identity. No inference, durable delivery,
checkpoint advance or real source authority is implied.

This is a rare recovery operation and repeats full verification for each
requested shard; it is not a normal incremental refresh performance claim.
The ordinary materializer/bundle outputs are unchanged. No schema or
external API changes. Tests compare selected metadata to independently
verified actual synthetic bundle rows and demonstrate unrelated-refresh
content identity stability.
