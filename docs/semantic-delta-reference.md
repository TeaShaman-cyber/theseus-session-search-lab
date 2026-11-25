# Semantic delta reference experiments (issue #59)

Session Search already imports and normalizes captures, preserves accepted
artifacts, assigns conversations to branches, rebuilds the SQLite projection,
and materializes Neon generations. Its local session reader and pinned Neon
session_browse read the selected conversation. No new reader is needed for
these experiments.

The three tests in tests/test_semantic_delta_reference.py exercise the existing
official ChatGPT export adapter and cumulative ingestion. The reference oracle
is the existing semantic_snapshot and full verify/rebuild, not a second
production projection. The test-only routing candidate uses the canonical
build_official_families and route_artifact functions. It reuses old routes
outside changed family evidence and compares against plan_projection_routes
on the whole accepted set.

Results:

- A repeated export with a different selected current leaf creates four new
  artifact identities in the two-conversation fixture. Provenance grows.
  Projected conversation contents and the canonical Neon source projection
  digest stay unchanged, although accepted-set digest and generation change.
  No old route requires reevaluation in this fixture.
- A single-leaf family later develops another branch. Its previously accepted
  official artifact and raw capture must be considered again. A message from
  the old raw capture moves from the base to the alternate branch; the
  unrelated stable family retains its contents and routes. The scoped route
  candidate agrees with the full planner.
- A changed message time can leave the family routing evidence entirely
  unchanged, but alters the projected conversation. A conversation absent from
  the newer export remains in the cumulative accepted corpus. Routing equality
  is not analysis equality or permission to skip a conversation.

All fixtures compare the final complete semantic snapshot, including artifact
and source links, with a fresh rebuild. The test comparison of conversation
contents excludes custody fields (accepted times and title-source artifact);
it is a conservative reference for content, order, title and coverage, not a
definition of all future Dreaming task dependencies.

This is a correctness experiment, not an incremental writer or speedup.
The candidate still computes old and new official families and full old
routes from in-memory normalized artifacts. It does not persist state or
optimize normalization, provenance registration, projection updates,
verification or transport. Tests are synthetic and use the existing Neon
manifest generator, not a new cloud deployment. An actual optimization must
preserve provenance, coverage, exact message identity and canonical rebuild
equivalence, including multimodal promotion and anonymous identities.

Next: reuse the existing family routing dependency and manifest/refresh path
in #59 to record changed projected conversations after accepted refresh.
Separate metadata/provenance-only changes from content-dependent analysis
work; verify affected closure against the existing rebuild oracle before
changing refresh behavior. Read selected history through existing readers.
Do not infer freshness or source completeness from a changed generation.

## Optional report from the existing import

The normal official-export importer now optionally returns a
`session_delta` object:

    python3 -m session_search.chatgpt_export EXPORT.zip --corpus CORPUS --report-session-delta

The default invocation and import result remain unchanged. While holding the
existing mutation lock, the reconciled import compares the old projection and
the already constructed candidate using the existing canonical session and
message stream queries. Hashes are per session; content, order, role, type,
message identity, time, title and coverage participate. Artifact custody and
acceptance times do not. There is no additional persistent index or new
rebuild for the comparison. The opt-in costs two sequential projection
scans (one for bootstrap), plus small per-session digest state.

The report returns sorted added/changed/removed session IDs, previous and
current accepted-set digests, baseline verification level, and explicitly
unknown source currentness. BOOTSTRAP denotes an uninitialized local corpus,
OBSERVED a comparison against the existing integrity-checked projection,
NO_NEW_ARTIFACTS an exact import replay, and BASELINE_UNVERIFIED means the
old projection was not verified and the lists are null. The existing final
derivation verification remains required for a newly published import.
A failed comparison aborts before ledger publication. The old projection's
integrity check is not a proof of its full derivation; OBSERVED is deliberately
not an authoritative complete source feed. Provenance-only additions may
require work for consumers whose task depends on provenance.

The report is an ordinary command result, not a durable delivery receipt.
If a caller loses it after successful publication, an import replay cannot
reconstruct the prior transition. It must not acknowledge a Dreaming cursor
or conclude that no pending analysis exists. Durable handoff and exact
source-to-serving-generation binding remain separate acceptance requirements.
No raw message bodies are returned; session IDs and accepted-set digests
still need private custody. No change to Neon schema, serving endpoints,
source permissions or model inference.
