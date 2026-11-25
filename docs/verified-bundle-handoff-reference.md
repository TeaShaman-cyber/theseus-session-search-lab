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
