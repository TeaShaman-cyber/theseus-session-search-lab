# Multi-Session Corpus Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cumulative Session Search corpus that idempotently ingests many portable session artifacts, preserves durable artifact membership/provenance, and exposes one rebuildable SQLite/FTS search surface.

**Architecture:** Reuse the existing Barn Doctor validation/normalization path through a new pure artifact-normalization module. Durable corpus membership lives in a content-addressed artifact store plus accepted-artifact ledger; SQLite/FTS remains a disposable materialized projection. Mutations are serialized with a corpus writer lock, and search remains read-only.

**Tech Stack:** Python 3.11 standard library (`argparse`, `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `sqlite3`, `tempfile`, `zipfile`), SQLite FTS5, `unittest`, existing GitHub Actions docs gate.

**Spec:** `docs/superpowers/specs/2026-09-01-multi-session-corpus-ingest-design.md`

## Global Constraints

- Preserve: `Session history is evidence. Search indexes are projections, not authority.`
- Barn Doctor and Google Drive remain replaceable adapters; corpus logic must not import connector/runtime code.
- Real captures, private message text, provider IDs, Drive IDs, cursors, and real artifact hashes never enter the public repository.
- `--corpus` overrides `SESSION_SEARCH_CORPUS`; if neither exists, corpus location resolution fails explicitly.
- Corpus membership requires a verified accepted-ledger entry; an artifact blob alone is not accepted membership.
- Searchable state must never contain an unaccepted artifact.
- Dedup identity for identified messages is `(session_id, message_id)`.
- Store both `source_object_sha256` and versioned `canonical_message_sha256`; only the canonical digest decides duplicate conflict.
- Messages without provider IDs are not deduplicated across artifacts in v1.
- `ingest` and `rebuild` use an exclusive corpus mutation lock; search does not.
- Session coverage remains conservative; v1 never proves completeness by heuristically stitching arbitrary partial captures.
- Rebuild reads only accepted-ledger members and must reproduce semantic/provenance/search invariants independently of discovery order.
- Existing one-artifact `session_search.importer` and `--db` search remain backward compatible.
- TDD is mandatory: observe RED before production changes for every task.

---

## File Structure

- Create `session_search/artifact.py` — portable artifact validation, stable session resolution, normalized page/message model, dual digest calculation.
- Modify `session_search/importer.py` — keep legacy single-projection CLI/API, but consume `artifact.normalize_artifact()` instead of owning duplicate normalization logic.
- Create `session_search/corpus_store.py` — corpus layout, SQLite schema, accepted ledger, mutation lock, transactional ingest, verify, rebuild.
- Create `session_search/corpus.py` — thin CLI for `ingest`, `verify`, `rebuild`, and lock diagnostics.
- Modify `session_search/search.py` — resolve `--corpus` / `SESSION_SEARCH_CORPUS`, add session metadata and optional session filter while preserving `--db`.
- Create `tests/test_artifact_normalization.py` — artifact/session/canonicalization fixtures and backward-compatibility-sensitive normalization tests.
- Create `tests/test_corpus_store.py` — multi-session, idempotency, conflict, coverage, ledger, lock, reconciliation, rebuild tests.
- Create `tests/test_corpus_search.py` — global multi-session search, provenance metadata, filters, default corpus resolution.
- Modify `tests/test_bootstrap_contract.py` only when needed to assert legacy behavior remains unchanged.
- Modify `README.md` — document the normal corpus happy path, default corpus environment variable, verify/rebuild, and legacy `--db` compatibility.
- Add sanitized implementation receipt under `receipts/003-multi-session-corpus.public.json` after private real-world acceptance.

---

### Task 1: Extract a reusable, versioned portable-artifact normalization model

**Files:**
- Create: `session_search/artifact.py`
- Modify: `session_search/importer.py`
- Create: `tests/test_artifact_normalization.py`
- Test: `tests/test_bootstrap_contract.py`

**Interfaces:**
- Produces `NormalizedArtifact`, `NormalizedPage`, `NormalizedMessage`, and `MessageSource` dataclasses.
- Produces `normalize_artifact(source: pathlib.Path) -> NormalizedArtifact`.
- Produces `canonical_message_object(message: dict) -> dict` and `CANONICAL_MESSAGE_VERSION = "session-search-message-v1"`.
- Legacy `import_export(source: pathlib.Path, db_path: pathlib.Path) -> dict` remains callable with the same signature and result keys used by existing tests.

- [ ] **Step 1: Add failing canonicalization and session-identity tests**

Create `tests/test_artifact_normalization.py` with fixtures containing two captures of the same identified message where only irrelevant provider metadata differs, plus mixed/missing session identity cases:

```python
import copy
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


class ArtifactNormalizationTest(unittest.TestCase):
    def test_irrelevant_source_metadata_changes_raw_digest_not_canonical_digest(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            first = td / "first.zip"
            second = td / "second.zip"
            base = {
                "id": "m1",
                "author": {"role": "assistant", "name": None},
                "create_time": 10.0,
                "content": {"content_type": "text", "parts": ["same semantic payload"]},
                "metadata": {"ui_flag": "old"},
            }
            changed = copy.deepcopy(base)
            changed["metadata"]["ui_flag"] = "new"
            write_capture(first, "session-a", [base])
            write_capture(second, "session-a", [changed])
            a = normalize_artifact(first).messages[0]
            b = normalize_artifact(second).messages[0]
            self.assertNotEqual(a.sources[0].source_object_sha256, b.sources[0].source_object_sha256)
            self.assertEqual(a.canonical_message_sha256, b.canonical_message_sha256)

    def test_changed_semantic_payload_changes_canonical_digest(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            first = td / "first.zip"
            second = td / "second.zip"
            base = {
                "id": "m1",
                "author": {"role": "assistant"},
                "create_time": 10.0,
                "content": {"content_type": "text", "parts": ["before"]},
            }
            changed = copy.deepcopy(base)
            changed["content"]["parts"] = ["after"]
            write_capture(first, "session-a", [base])
            write_capture(second, "session-a", [changed])
            a = normalize_artifact(first).messages[0]
            b = normalize_artifact(second).messages[0]
            self.assertNotEqual(a.canonical_message_sha256, b.canonical_message_sha256)

    def test_unresolved_session_id_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "BLOCKED_UNRESOLVED_SESSION_ID"):
            normalize_artifact(make_capture_without_conversation_id())

    def test_mixed_session_artifact_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "BLOCKED_MIXED_SESSION_ARTIFACT"):
            normalize_artifact(make_capture_with_two_conversation_ids())
```

Implement concrete local helper functions in the test file (`write_capture`, `make_capture_without_conversation_id`, `make_capture_with_two_conversation_ids`) using the same manifest SHA/size pattern already present in `tests/test_bootstrap_contract.py`; do not depend on real captures.

- [ ] **Step 2: Run the new tests and observe RED**

Run:

```bash
python3 -m unittest -v tests.test_artifact_normalization
```

Expected: import failure because `session_search.artifact` does not exist.

- [ ] **Step 3: Implement the normalized dataclasses and dual-digest contract**

Create `session_search/artifact.py` with these exact public shapes:

```python
CANONICAL_MESSAGE_VERSION = "session-search-message-v1"

@dataclasses.dataclass(frozen=True)
class MessageSource:
    capture_sequence: int
    page_position: int
    member_name: str
    source_object_sha256: str

@dataclasses.dataclass(frozen=True)
class NormalizedMessage:
    message_id: str | None
    role: str
    content_type: str
    search_class: str
    create_time: float | None
    text: str
    canonical_message_sha256: str
    sources: tuple[MessageSource, ...]

@dataclasses.dataclass(frozen=True)
class NormalizedPage:
    capture_sequence: int
    member_name: str
    start_cursor: str | None
    end_cursor: str | None
    has_previous_page: bool
    has_next_page: bool
    message_count: int
    min_create_time: float | None
    max_create_time: float | None

@dataclasses.dataclass(frozen=True)
class NormalizedArtifact:
    source: pathlib.Path
    artifact_sha256: str
    size_bytes: int
    source_schema: str
    source_adapter: str
    session_id: str
    title: str
    coverage_state: str
    has_previous_page: bool
    has_next_page: bool
    pages: tuple[NormalizedPage, ...]
    messages: tuple[NormalizedMessage, ...]
    message_occurrences: int
    duplicate_message_occurrences: int
```

Canonicalization must be deterministic JSON over exactly this object:

```python
def canonical_message_object(message: dict) -> dict:
    author = message.get("author") or {}
    content = message.get("content") or {}
    return {
        "version": CANONICAL_MESSAGE_VERSION,
        "author_role": str(author.get("role") or "unknown"),
        "content_type": str(content.get("content_type") or "unknown"),
        "content": normalize_semantic_content(content),
    }
```

`normalize_semantic_content()` must preserve actual semantic content rather than just search text:

```python
def normalize_semantic_content(content: dict):
    ctype = str(content.get("content_type") or "unknown")
    if ctype == "text":
        return {"parts": content.get("parts") or []}
    if ctype == "code":
        return {"text": content.get("text") or ""}
    if ctype == "tether_browsing_display":
        return {"text": content.get("text"), "result": content.get("result")}
    return content
```

Reuse/move the existing safe ZIP, manifest verification, text extraction, classification, pagination, chronology, and duplicate-object checks from `importer.py`; preserve identical-duplicate rejection semantics inside one artifact.

- [ ] **Step 4: Refactor legacy importer to consume `normalize_artifact()`**

Keep `init_db()` and the legacy projection schema in `importer.py`. Replace raw ZIP parsing inside `import_export()` with iteration over `NormalizedArtifact.pages/messages`, preserving current return keys:

```python
artifact = normalize_artifact(source)
# build legacy one-session DB from artifact.pages/messages
indexed = sum(1 for message in artifact.messages if message.text and message.search_class != "hidden")
class_counts: dict[str, int] = {}
for message in artifact.messages:
    class_counts[message.search_class] = class_counts.get(message.search_class, 0) + 1
return {
    "coverage_state": artifact.coverage_state,
    "has_previous_page": artifact.has_previous_page,
    "has_next_page": artifact.has_next_page,
    "messages": len(artifact.messages),
    "message_occurrences": artifact.message_occurrences,
    "duplicate_message_occurrences": artifact.duplicate_message_occurrences,
    "payload_pages": len(artifact.pages),
    "indexed": indexed,
    "search_classes": class_counts,
    "integrity": integrity,
}
```

The legacy importer may still delete/recreate its requested scratch DB; only the new corpus path removes that lifecycle limitation.

- [ ] **Step 5: Run normalization plus legacy regression tests**

Run:

```bash
python3 -m unittest -v tests.test_artifact_normalization tests.test_bootstrap_contract
```

Expected: PASS, including the existing five bootstrap tests.

- [ ] **Step 6: Commit Task 1**

```bash
git add session_search/artifact.py session_search/importer.py tests/test_artifact_normalization.py tests/test_bootstrap_contract.py
git commit -m "refactor: normalize portable session artifacts (refs #7)"
```

---

### Task 2: Add corpus layout, accepted ledger, schema, path resolution, and writer lock primitives

**Files:**
- Create: `session_search/corpus_store.py`
- Create: `tests/test_corpus_store.py`

**Interfaces:**
- `resolve_corpus_root(explicit: str | None, env: Mapping[str, str] | None = None) -> pathlib.Path`
- `CorpusPaths.from_root(root: pathlib.Path) -> CorpusPaths`
- `CorpusMutationLock(paths: CorpusPaths, operation: str)` context manager
- `init_corpus_db(conn: sqlite3.Connection) -> None`
- `read_accepted_ledger(paths: CorpusPaths) -> dict[str, dict]`
- `write_accepted_entry(paths: CorpusPaths, entry: dict) -> pathlib.Path`
- `accepted_entry_path(paths: CorpusPaths, sha256: str) -> pathlib.Path`

- [ ] **Step 1: Write failing path, lock, ledger, and schema tests**

Create `tests/test_corpus_store.py` beginning with:

```python
class CorpusPrimitiveTest(unittest.TestCase):
    def test_explicit_path_overrides_environment_default(self):
        env = {"SESSION_SEARCH_CORPUS": "/env/corpus"}
        self.assertEqual(
            resolve_corpus_root("/explicit/corpus", env),
            pathlib.Path("/explicit/corpus"),
        )

    def test_missing_corpus_location_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "CORPUS_LOCATION_UNRESOLVED"):
            resolve_corpus_root(None, {})

    def test_second_writer_fails_fast_without_breaking_first_lock(self):
        with tempfile.TemporaryDirectory() as td:
            paths = CorpusPaths.from_root(pathlib.Path(td))
            paths.ensure_layout()
            with CorpusMutationLock(paths, "ingest"):
                with self.assertRaisesRegex(RuntimeError, "CORPUS_MUTATION_LOCKED"):
                    with CorpusMutationLock(paths, "rebuild"):
                        pass

    def test_accepted_entry_is_atomic_and_hash_addressed(self):
        with tempfile.TemporaryDirectory() as td:
            paths = CorpusPaths.from_root(pathlib.Path(td))
            paths.ensure_layout()
            sha = "a" * 64
            entry = {
                "schema": "theseus.session-search-accepted-artifact.v1",
                "artifact_sha256": sha,
                "size_bytes": 123,
                "session_id": "session-a",
                "coverage_state": "PARTIAL_SESSION_SLICE",
                "source_schema": "barn-doctor-export:v1",
                "source_adapter": "barn-doctor",
                "accepted_at": "2026-09-01T00:00:00+00:00",
            }
            path = write_accepted_entry(paths, entry)
            self.assertEqual(path, accepted_entry_path(paths, sha))
            self.assertEqual(json.loads(path.read_text()), entry)

    def test_schema_supports_many_sessions_and_scoped_message_identity(self):
        conn = sqlite3.connect(":memory:")
        init_corpus_db(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"corpus_meta", "artifacts", "sessions", "payload_pages", "messages", "message_sources", "messages_fts"} <= tables)
        index_sql = "\n".join(row[0] or "" for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='index'"))
        self.assertIn("session_id", index_sql)
        self.assertIn("message_id", index_sql)
```

- [ ] **Step 2: Run primitive tests and observe RED**

```bash
python3 -m unittest -v tests.test_corpus_store.CorpusPrimitiveTest
```

Expected: import failure because `session_search.corpus_store` does not exist.

- [ ] **Step 3: Implement corpus paths and location resolution**

Use:

```python
@dataclasses.dataclass(frozen=True)
class CorpusPaths:
    root: pathlib.Path
    artifacts_sha256: pathlib.Path
    accepted_ledger: pathlib.Path
    ingest_receipts: pathlib.Path
    rebuild_receipts: pathlib.Path
    staging: pathlib.Path
    mutation_lock: pathlib.Path
    db: pathlib.Path

    @classmethod
    def from_root(cls, root: pathlib.Path) -> "CorpusPaths":
        root = root.expanduser()
        return cls(
            root=root,
            artifacts_sha256=root / "artifacts" / "sha256",
            accepted_ledger=root / "ledger" / "accepted",
            ingest_receipts=root / "receipts" / "ingest",
            rebuild_receipts=root / "receipts" / "rebuild",
            staging=root / "staging",
            mutation_lock=root / "mutation.lock",
            db=root / "corpus.sqlite3",
        )

    def ensure_layout(self) -> None:
        for directory in (
            self.artifacts_sha256,
            self.accepted_ledger,
            self.ingest_receipts,
            self.rebuild_receipts,
            self.staging,
        ):
            directory.mkdir(parents=True, exist_ok=True)
```

`resolve_corpus_root()` precedence must exactly match the spec. Do not create a hidden OS-specific default.

- [ ] **Step 4: Implement portable lock-directory ownership**

`CorpusMutationLock.__enter__()` uses atomic `mkdir()` on `mutation.lock/`, then writes `owner.json` containing `operation`, PID when available, host/runtime marker, and UTC start time. Existing lock means `CORPUS_MUTATION_LOCKED`. `__exit__()` removes only the lock currently owned by this instance. No stale-lock auto-break behavior.

- [ ] **Step 5: Implement versioned corpus SQLite schema**

`init_corpus_db()` creates `corpus_meta`, `artifacts`, `sessions`, `payload_pages`, `messages`, `message_sources`, and `messages_fts`. Required uniqueness/foreign keys:

```sql
UNIQUE(sha256)                          -- artifacts
UNIQUE(session_id, message_id)          -- partial index WHERE message_id IS NOT NULL
UNIQUE(artifact_id, member_name)         -- payload_pages
FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
FOREIGN KEY(session_id) REFERENCES sessions(session_id)
FOREIGN KEY(message_row_id) REFERENCES messages(row_id)
FOREIGN KEY(page_id) REFERENCES payload_pages(page_id)
```

Store `canonical_message_sha256` on `messages` and per-occurrence `source_object_sha256` on `message_sources`.

- [ ] **Step 6: Implement accepted-ledger read/write**

Accepted entry v1 shape:

```python
entry = {
    "schema": "theseus.session-search-accepted-artifact.v1",
    "artifact_sha256": sha,
    "size_bytes": size,
    "session_id": session_id,
    "coverage_state": coverage,
    "source_schema": source_schema,
    "source_adapter": source_adapter,
    "accepted_at": utc_iso,
}
```

Write to staging + `os.replace()` into `ledger/accepted/<sha>.json`; then reopen and verify `artifact_sha256`, `size_bytes`, and schema before treating the write as successful.

- [ ] **Step 7: Run primitive tests**

```bash
python3 -m unittest -v tests.test_corpus_store.CorpusPrimitiveTest
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add session_search/corpus_store.py tests/test_corpus_store.py
git commit -m "feat: add Session Search corpus primitives (refs #7)"
```

---

### Task 3: Implement transactional cumulative ingest, idempotency, dedup, and conservative coverage

**Files:**
- Modify: `session_search/corpus_store.py`
- Create: `session_search/corpus.py`
- Modify: `tests/test_corpus_store.py`

**Interfaces:**
- `ingest_artifact(source: pathlib.Path, corpus_root: pathlib.Path) -> dict`
- `ingest_many(sources: Sequence[pathlib.Path], corpus_root: pathlib.Path) -> dict`
- `apply_normalized_artifact_to_projection(conn: sqlite3.Connection, artifact: NormalizedArtifact, accepted_at: str) -> dict`
- `assert_transaction_invariants(conn: sqlite3.Connection, artifact: NormalizedArtifact) -> None`
- `verify_ingest_postconditions(paths: CorpusPaths, sha256: str) -> dict`
- `write_ingest_receipt(paths: CorpusPaths, artifact_sha256: str, status: str, postconditions: dict) -> pathlib.Path`
- CLI: `python3 -m session_search.corpus ingest <captures...> [--corpus PATH] [--json]`

- [ ] **Step 1: Add failing multi-session/idempotency/conflict tests**

Add synthetic capture builders for session A/B and repeated captures. Required tests:

```python
class CorpusIngestTest(unittest.TestCase):
    def test_two_sessions_coexist_and_same_message_id_does_not_cross_collide(self):
        corpus, a, b = make_two_session_fixture(shared_message_id="m1")
        self.assertEqual(ingest_artifact(a, corpus)["status"], "INGESTED")
        self.assertEqual(ingest_artifact(b, corpus)["status"], "INGESTED")
        self.assertEqual(db_scalar(corpus, "SELECT count(*) FROM sessions"), 2)
        self.assertEqual(db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id='m1'"), 2)

    def test_same_artifact_sha_is_verified_noop(self):
        corpus, capture = make_single_session_fixture()
        first = ingest_artifact(capture, corpus)
        before = corpus_counts(corpus)
        second = ingest_artifact(capture, corpus)
        self.assertEqual(first["status"], "INGESTED")
        self.assertEqual(second["status"], "ALREADY_INGESTED")
        self.assertEqual(second["mutation"], "none")
        self.assertEqual(corpus_counts(corpus), before)

    def test_repeated_same_session_capture_adds_only_novel_messages_and_provenance(self):
        corpus, first, second = make_repeated_session_fixture()
        ingest_artifact(first, corpus)
        before = corpus_counts(corpus)
        result = ingest_artifact(second, corpus)
        self.assertEqual(result["status"], "INGESTED")
        self.assertEqual(corpus_counts(corpus)["messages"], before["messages"] + 1)
        self.assertGreater(corpus_counts(corpus)["message_sources"], before["message_sources"])

    def test_same_canonical_message_with_changed_irrelevant_metadata_deduplicates(self):
        corpus, first, metadata_variant = make_metadata_variant_fixture()
        ingest_artifact(first, corpus)
        ingest_artifact(metadata_variant, corpus)
        self.assertEqual(db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id='m1'"), 1)
        digests = db_column(corpus, "SELECT DISTINCT source_object_sha256 FROM message_sources")
        self.assertEqual(len(digests), 2)

    def test_changed_canonical_payload_for_same_session_message_rolls_back(self):
        corpus, first, conflicting = make_conflicting_payload_fixture()
        ingest_artifact(first, corpus)
        before = corpus_counts(corpus)
        with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
            ingest_artifact(conflicting, corpus)
        self.assertEqual(corpus_counts(corpus), before)

    def test_anonymous_messages_from_two_artifacts_are_not_merged(self):
        corpus, first, second = make_anonymous_message_fixture()
        ingest_artifact(first, corpus)
        ingest_artifact(second, corpus)
        self.assertEqual(db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id IS NULL"), 2)

    def test_failed_ingest_leaves_prior_counts_and_fts_unchanged(self):
        corpus, good, bad = make_good_and_conflicting_fixture()
        ingest_artifact(good, corpus)
        before_counts = corpus_counts(corpus)
        before_hits = fts_count(corpus, "stable phrase")
        with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
            ingest_artifact(bad, corpus)
        self.assertEqual(corpus_counts(corpus), before_counts)
        self.assertEqual(fts_count(corpus, "stable phrase"), before_hits)

    def test_partial_complete_and_later_tail_coverage_transitions_are_conservative(self):
        corpus, partial, complete, later_tail, newer_complete = make_coverage_transition_fixture()
        ingest_artifact(partial, corpus)
        self.assertEqual(session_coverage(corpus), "PARTIAL_SESSION_SLICE")
        ingest_artifact(complete, corpus)
        self.assertEqual(session_coverage(corpus), "COMPLETE_EXPOSED_CONVERSATION")
        ingest_artifact(later_tail, corpus)
        self.assertEqual(session_coverage(corpus), "PARTIAL_SESSION_SLICE")
        ingest_artifact(newer_complete, corpus)
        self.assertEqual(session_coverage(corpus), "COMPLETE_EXPOSED_CONVERSATION")

    def test_unaccepted_blob_after_failed_ingest_is_not_registered(self):
        corpus, good, bad = make_good_and_conflicting_fixture()
        ingest_artifact(good, corpus)
        with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
            ingest_artifact(bad, corpus)
        bad_sha = file_sha256(bad)
        self.assertFalse(accepted_entry_path(CorpusPaths.from_root(corpus), bad_sha).exists())
        self.assertEqual(db_scalar(corpus, "SELECT count(*) FROM artifacts WHERE sha256=?", (bad_sha,)), 0)
```

Use only synthetic artifact fixtures. Define the following test-only helpers in `tests/test_corpus_store.py` before the test classes, implemented with the synthetic ZIP writer from Task 1: `make_two_session_fixture(shared_message_id: str)`, `make_single_session_fixture()`, `make_repeated_session_fixture()`, `make_metadata_variant_fixture()`, `make_conflicting_payload_fixture()`, `make_anonymous_message_fixture()`, `make_good_and_conflicting_fixture()`, `make_coverage_transition_fixture()`, `db_scalar(corpus, sql, params=())`, `db_column(corpus, sql, params=())`, `corpus_counts(corpus)`, `fts_count(corpus, phrase)`, `session_coverage(corpus)`, and `file_sha256(path)`. Each fixture uses `tempfile.TemporaryDirectory()` retained by the test object and returns concrete `pathlib.Path` objects. For rollback assertions, capture pre/post counts from `sessions`, `messages`, `payload_pages`, `artifacts`, and FTS hits.

- [ ] **Step 2: Run ingest tests and observe RED**

```bash
python3 -m unittest -v tests.test_corpus_store.CorpusIngestTest
```

Expected: failures because `ingest_artifact()` is absent.

- [ ] **Step 3: Implement content-addressed artifact placement**

Flow:

```python
artifact = normalize_artifact(source)
sha = artifact.artifact_sha256
final_blob = paths.artifacts_sha256 / f"{sha}.zip"
# copy source -> unique staging temp, fsync/close, hash verify, os.replace if absent
```

If the blob already exists, verify its SHA and size rather than overwriting blindly.

- [ ] **Step 4: Implement transaction-local dedup/conflict/provenance logic**

Inside one SQLite transaction:

```python
existing = find_message(session_id, message_id)
if existing is None:
    insert canonical message + FTS row
elif existing.canonical_message_sha256 == incoming.canonical_message_sha256:
    reuse row_id
else:
    raise CorpusError("FAILED_CONFLICTING_DUPLICATE")
# always add page/source provenance for accepted occurrences
```

Anonymous messages always create independent canonical rows.

- [ ] **Step 5: Implement conservative touched-session metadata and coverage recomputation**

Compute title/time metadata from accepted evidence using the deterministic rules in the spec. Coverage logic must satisfy:

```python
complete_artifact_without_external_novel_messages -> COMPLETE_EXPOSED_CONVERSATION
only_partial_artifacts -> PARTIAL_SESSION_SLICE
complete_then_later_partial_with_new_messages -> PARTIAL_SESSION_SLICE
newer_complete_covering_expanded_message_set -> COMPLETE_EXPOSED_CONVERSATION
```

Do not stitch partial artifacts by timestamps alone.

- [ ] **Step 6: Implement accepted-ledger-before-DB-commit ordering**

Required ordering in production code:

```python
conn.execute("BEGIN IMMEDIATE")
apply_projection_changes(conn, artifact)
assert_transaction_invariants(conn, artifact)
ledger_path = write_accepted_entry(paths, entry)
verify_accepted_entry(ledger_path, entry)
conn.commit()
verify_ingest_postconditions(paths, sha)
write_ingest_receipt(
    paths,
    artifact_sha256=sha,
    status="INGESTED",
    postconditions=verify_ingest_postconditions(paths, sha),
)
```

If ledger write/readback fails, rollback SQLite. If failure happens after ledger acceptance but before commit, return/record `RECONCILIATION_REQUIRED`; never report `INGESTED` without readback that ledger and DB agree.

- [ ] **Step 7: Implement verified idempotent no-op**

If accepted ledger + artifact bytes + DB artifact row agree:

```python
{
    "status": "ALREADY_INGESTED",
    "mutation": "none",
    "artifact_sha256": sha,
    "session_id": accepted_entry["session_id"],
}
```

If any membership surface disagrees, return `RECONCILIATION_REQUIRED`; do not mutate.

- [ ] **Step 8: Add the thin corpus ingest CLI**

Create `session_search/corpus.py` using `argparse` subparsers. `ingest` resolves the root with `resolve_corpus_root()`, then calls `ingest_many()`. JSON mode prints the combined summary; non-JSON mode prints one concise status line per artifact without raw message text.

- [ ] **Step 9: Run ingest tests plus all legacy tests**

```bash
python3 -m unittest -v tests.test_corpus_store.CorpusIngestTest tests.test_artifact_normalization tests.test_bootstrap_contract
```

Expected: PASS.

- [ ] **Step 10: Commit Task 3**

```bash
git add session_search/corpus_store.py session_search/corpus.py tests/test_corpus_store.py
git commit -m "feat: ingest cumulative Session Search corpus (refs #7)"
```

---

### Task 4: Implement corpus verify, reconciliation detection, and deterministic rebuild

**Files:**
- Modify: `session_search/corpus_store.py`
- Modify: `session_search/corpus.py`
- Modify: `tests/test_corpus_store.py`

**Interfaces:**
- Consumes `apply_normalized_artifact_to_projection()` from Task 3 without rewriting accepted membership.
- `verify_corpus(corpus_root: pathlib.Path) -> dict`
- `rebuild_corpus(corpus_root: pathlib.Path) -> dict`
- `read_lock_status(paths: CorpusPaths) -> dict`
- `semantic_snapshot(corpus_root: pathlib.Path) -> dict` as a private test/helper normalization of rebuild-relevant state.
- CLI: `python3 -m session_search.corpus verify [--corpus PATH] [--json]`
- CLI: `python3 -m session_search.corpus rebuild [--corpus PATH] [--json]`
- CLI: `python3 -m session_search.corpus lock-status [--corpus PATH] [--json]`

- [ ] **Step 1: Add failing verify/rebuild/reconciliation tests**

Add:

```python
class CorpusVerifyRebuildTest(unittest.TestCase):
    def test_verify_detects_missing_or_tampered_accepted_artifact(self):
        corpus, capture = make_single_session_fixture()
        result = ingest_artifact(capture, corpus)
        blob = CorpusPaths.from_root(corpus).artifacts_sha256 / f"{result['artifact_sha256']}.zip"
        blob.write_bytes(b"tampered")
        self.assertEqual(verify_corpus(corpus)["status"], "FAILED_INTEGRITY")

    def test_verify_detects_ledger_projection_membership_disagreement(self):
        corpus, capture = make_single_session_fixture()
        result = ingest_artifact(capture, corpus)
        with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
            conn.execute("DELETE FROM artifacts WHERE sha256=?", (result["artifact_sha256"],))
            conn.commit()
        self.assertEqual(verify_corpus(corpus)["status"], "RECONCILIATION_REQUIRED")

    def test_blob_without_accepted_ledger_is_excluded_from_rebuild(self):
        corpus, accepted, orphan = make_accepted_and_orphan_blob_fixture()
        ingest_artifact(accepted, corpus)
        copy_blob_into_artifact_store(orphan, corpus)
        rebuild_corpus(corpus)
        self.assertEqual(db_scalar(corpus, "SELECT count(*) FROM artifacts"), 1)

    def test_rebuild_reproduces_semantic_and_provenance_invariants(self):
        corpus, captures = make_three_artifact_fixture()
        for capture in captures:
            ingest_artifact(capture, corpus)
        before = semantic_snapshot(corpus)
        result = rebuild_corpus(corpus)
        self.assertEqual(result["status"], "REBUILT")
        self.assertEqual(semantic_snapshot(corpus), before)

    def test_rebuild_result_is_independent_of_artifact_discovery_order(self):
        left, right, captures = make_two_corpus_rebuild_fixture()
        for capture in captures:
            ingest_artifact(capture, left)
        for capture in reversed(captures):
            ingest_artifact(capture, right)
        rebuild_corpus(left)
        rebuild_corpus(right)
        self.assertEqual(semantic_snapshot(left), semantic_snapshot(right))

    def test_rebuild_failure_keeps_old_projection_untouched(self):
        corpus, capture = make_single_session_fixture()
        ingest_artifact(capture, corpus)
        before = CorpusPaths.from_root(corpus).db.read_bytes()
        tamper_one_accepted_blob(corpus)
        with self.assertRaisesRegex(RuntimeError, "FAILED_INTEGRITY"):
            rebuild_corpus(corpus)
        self.assertEqual(CorpusPaths.from_root(corpus).db.read_bytes(), before)

    def test_lock_status_reports_owner_without_breaking_lock(self):
        corpus = pathlib.Path(tempfile.mkdtemp())
        paths = CorpusPaths.from_root(corpus)
        paths.ensure_layout()
        with CorpusMutationLock(paths, "ingest"):
            status = read_lock_status(paths)
            self.assertEqual(status["operation"], "ingest")
            self.assertTrue(paths.mutation_lock.exists())
        self.assertFalse(paths.mutation_lock.exists())
```

Define these additional test-only helpers in `tests/test_corpus_store.py`: `make_accepted_and_orphan_blob_fixture()`, `copy_blob_into_artifact_store(source, corpus)`, `make_three_artifact_fixture()`, `make_two_corpus_rebuild_fixture()`, `tamper_one_accepted_blob(corpus)`, and `semantic_snapshot(corpus)`. `semantic_snapshot()` returns sorted JSON-compatible rows for sessions, artifacts, messages, message_sources, coverage, FTS searchable-row count, and fixed synthetic canary hit counts. For order independence, build two corpora from the same accepted synthetic artifacts with reversed discovery/listing order and compare these normalized snapshots.

- [ ] **Step 2: Run verify/rebuild tests and observe RED**

```bash
python3 -m unittest -v tests.test_corpus_store.CorpusVerifyRebuildTest
```

Expected: failures because verify/rebuild interfaces are absent.

- [ ] **Step 3: Implement `verify_corpus()`**

At minimum verify:

```python
sqlite_integrity == "ok"
accepted_ledger_sha_set == db_artifact_sha_set
all accepted blobs exist and hash correctly
no DB artifact row lacks ledger membership
identified message uniqueness invariant
foreign_key_check returns no rows
fts_row_count == searchable_message_count
stored session coverage == recomputed coverage
```

Return `status="VERIFIED"` only if every check passes. Membership disagreement returns `RECONCILIATION_REQUIRED` and names only safe local artifact digests/paths in private output.

- [ ] **Step 4: Implement deterministic rebuild from accepted ledger only**

Under `CorpusMutationLock(paths, "rebuild")`:

```python
entries = sorted(read_accepted_ledger(paths).values(), key=lambda e: e["artifact_sha256"])
new_db = paths.root / "corpus.sqlite3.new"
# initialize empty DB
# replay each accepted artifact into projection-only builder without rewriting ledger/receipts
# verify new projection against accepted ledger/artifacts
# compare semantic snapshot with old projection where present
# os.replace(new_db, paths.db) only after verification
```

Do not call normal `ingest_artifact()` if it would rewrite ledger membership; expose an internal `apply_normalized_artifact_to_projection()` used by both ingest and rebuild.

- [ ] **Step 5: Implement deterministic session metadata selection**

During projection replay:

```python
# title source: artifact with greatest observed source-message time
# no-time artifacts sort before timed artifacts
# ties: lexicographically smallest artifact SHA
# first/last message time: min/max canonical message create_time
# accepted_at: copied from durable ledger entry, never replay wall clock
```

- [ ] **Step 6: Implement safe swap and lock diagnostics**

`os.replace(corpus.sqlite3.new, corpus.sqlite3)` occurs only after successful verify. If replace fails because a platform reader holds the old DB, preserve both files and return `REBUILD_SWAP_BLOCKED`. `lock-status` only reads `mutation.lock/owner.json`; it never deletes the lock.

- [ ] **Step 7: Run verify/rebuild tests and full suite**

```bash
python3 -m unittest -v tests.test_corpus_store tests.test_artifact_normalization tests.test_bootstrap_contract
```

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

```bash
git add session_search/corpus_store.py session_search/corpus.py tests/test_corpus_store.py
git commit -m "feat: verify and rebuild Session Search corpus (refs #7)"
```

---

### Task 5: Make search corpus-aware while preserving legacy `--db`

**Files:**
- Modify: `session_search/search.py`
- Create: `tests/test_corpus_search.py`

**Interfaces:**
- Preserve `fts_query(text: str) -> str`.
- Preserve legacy `search(db: str, query: str, scopes: list[str], limit: int) -> list[dict]` for existing callers.
- Add `search_corpus(corpus_root: pathlib.Path, query: str, scopes: list[str], limit: int, session_id: str | None = None) -> list[dict]`.
- CLI supports mutually exclusive `--db PATH` and `--corpus PATH`; with neither, `SESSION_SEARCH_CORPUS` may resolve the corpus.

- [ ] **Step 1: Add failing corpus search tests**

Create `tests/test_corpus_search.py`. Define test-only helpers `build_search_fixture_two_sessions(shared_phrase: str)`, `run_search_cli(query: str, corpus: pathlib.Path | None = None, env: dict[str, str] | None = None)`, and `build_legacy_single_projection_fixture()` using synthetic artifacts/imports from earlier tasks; then add:

```python
class CorpusSearchTest(unittest.TestCase):
    def test_global_search_returns_hits_from_multiple_sessions_with_coverage(self):
        corpus = build_search_fixture_two_sessions(shared_phrase="copper compass")
        rows = search_corpus(corpus, "copper compass", ["dialogue", "evidence"], 10)
        self.assertEqual({row["session_id"] for row in rows}, {"session-a", "session-b"})
        self.assertTrue(all(row["session_coverage"] in {"PARTIAL_SESSION_SLICE", "COMPLETE_EXPOSED_CONVERSATION"} for row in rows))

    def test_session_filter_limits_hits_to_one_session(self):
        corpus = build_search_fixture_two_sessions(shared_phrase="copper compass")
        rows = search_corpus(corpus, "copper compass", ["dialogue", "evidence"], 10, session_id="session-b")
        self.assertTrue(rows)
        self.assertEqual({row["session_id"] for row in rows}, {"session-b"})

    def test_explicit_corpus_overrides_environment_default(self):
        explicit = build_search_fixture_two_sessions(shared_phrase="explicit phrase")
        env_default = build_search_fixture_two_sessions(shared_phrase="environment phrase")
        result = run_search_cli("explicit phrase", corpus=explicit, env={"SESSION_SEARCH_CORPUS": str(env_default)})
        self.assertEqual(result.returncode, 0)
        self.assertIn("explicit phrase", result.stdout)

    def test_environment_default_enables_no_path_cli(self):
        corpus = build_search_fixture_two_sessions(shared_phrase="environment phrase")
        result = run_search_cli("environment phrase", env={"SESSION_SEARCH_CORPUS": str(corpus)})
        self.assertEqual(result.returncode, 0)
        self.assertIn("environment phrase", result.stdout)

    def test_legacy_db_search_still_returns_existing_shape(self):
        db = build_legacy_single_projection_fixture()
        rows = search(str(db), "legacy phrase", ["dialogue", "evidence"], 8)
        self.assertTrue(rows)
        self.assertEqual(set(rows[0]), {"ordinal", "message_id", "role", "content_type", "search_class", "text", "score"})
```

Each corpus hit must include these exact keys:

```python
required_keys = {
    "session_id",
    "session_title",
    "session_coverage",
    "ordinal",
    "message_id",
    "role",
    "content_type",
    "search_class",
    "create_time",
    "score",
    "text",
}
self.assertTrue(required_keys <= set(rows[0]))
```

- [ ] **Step 2: Run search tests and observe RED**

```bash
python3 -m unittest -v tests.test_corpus_search
```

Expected: missing corpus-search interface/options.

- [ ] **Step 3: Implement corpus query and CLI resolution**

Join `messages_fts -> messages -> sessions`; apply scopes and optional `session_id` before ordering by BM25 then per-session ordinal. Keep legacy `search()` untouched for `--db` callers.

CLI rules:

```text
--db and --corpus together -> error
--db only -> legacy path
--corpus -> corpus path
neither + SESSION_SEARCH_CORPUS -> corpus path
neither + no environment -> explicit location error
```

- [ ] **Step 4: Run search plus full public test suite**

```bash
python3 -m unittest -v tests.test_corpus_search tests.test_corpus_store tests.test_artifact_normalization tests.test_bootstrap_contract
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add session_search/search.py tests/test_corpus_search.py
git commit -m "feat: search across Session Search corpus (refs #7)"
```

---

### Task 6: Document the seamless workflow and run private three-artifact acceptance

**Files:**
- Modify: `README.md`
- Create: `receipts/003-multi-session-corpus.public.json`
- Private-only runtime outputs: `<private-corpus-root>/` and private acceptance receipt(s); do not commit them.

**Interfaces:**
- Public docs show one-time `SESSION_SEARCH_CORPUS` configuration, `corpus ingest`, normal search, `verify`, `rebuild`, and legacy `--db`.
- Public receipt contains only sanitized counts/statuses and exact implementation commit/run metadata; no real session IDs, titles, message IDs, filenames, Drive IDs, raw hashes, or conversation text.

- [ ] **Step 1: Add/extend documentation contract test before editing README**

In `tests/test_bootstrap_contract.py`, add assertions that README contains:

```python
self.assertIn("SESSION_SEARCH_CORPUS", text)
self.assertIn("session_search.corpus ingest", text)
self.assertIn("session_search.corpus verify", text)
self.assertIn("session_search.corpus rebuild", text)
self.assertIn("--db", text)
self.assertIn("Search indexes are projections, not authority", text)
```

Run the single test and observe RED because README has not yet been updated.

- [ ] **Step 2: Update README with the exact normal workflow**

Document:

```bash
export SESSION_SEARCH_CORPUS=/private/path/session-search-corpus
python3 -m session_search.corpus ingest capture.zip
python3 -m session_search.search "previous decision"
python3 -m session_search.corpus verify
```

Also document explicit `--corpus` override, `rebuild`, lock-status diagnostics, and legacy scratch `--db` behavior. State explicitly that corpus directories contain private evidence and must not be committed.

- [ ] **Step 3: Run the complete public verification gate**

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_repo.py
git diff --check
```

Expected: all tests PASS, repository verification PASS, no whitespace errors.

- [ ] **Step 4: Create a fresh private corpus from the three verified private artifacts**

Use a new private root, never the legacy full DB as authority:

```bash
export SESSION_SEARCH_CORPUS=<private-corpus-root>
python3 -m session_search.corpus ingest <private-case-A.zip> <private-case-B.zip> <private-case-C.zip> --json
python3 -m session_search.corpus verify --json
```

Expected postconditions from verified stable-session readback:

```text
sessions = 2
accepted ledger members = 3
canonical messages = 3789
case A and case B resolve to the same stable session
case A + B aggregate coverage = PARTIAL_SESSION_SLICE
case C resolves to a distinct session with COMPLETE_EXPOSED_CONVERSATION
sqlite integrity = ok
```

The earlier inference that zero message-ID overlap implied three distinct sessions was disproven by stable-session identity. If canonical counts or session relationships differ from these verified inputs, stop and investigate rather than editing the expected count silently.

- [ ] **Step 5: Prove cross-session search and rebuild on private data**

Run several known safe keyword canaries privately, including one expected in the newly captured Needle-related session material. Record only counts/session-count coverage in the public receipt, not hit text or identifiers.

Then:

```bash
cp "$SESSION_SEARCH_CORPUS/corpus.sqlite3" /tmp/corpus-before-rebuild.sqlite3
python3 -m session_search.corpus rebuild --json
python3 -m session_search.corpus verify --json
```

Compare semantic snapshots before/after rebuild: session count, canonical message count, per-session coverage multiset, artifact/provenance counts, FTS searchable-row count, and canary hit counts must match.

- [ ] **Step 6: Write sanitized public receipt**

Create `receipts/003-multi-session-corpus.public.json` with this shape:

```json
{
  "schema": "theseus.session-search-corpus-acceptance.public.v1",
  "issue": 7,
  "sessions": 2,
  "canonical_messages": 3789,
  "coverage_states": {
    "COMPLETE_EXPOSED_CONVERSATION": 1,
    "PARTIAL_SESSION_SLICE": 1
  },
  "accepted_artifacts": 3,
  "sqlite_integrity": "ok",
  "cross_session_search": "PASS",
  "rebuild_equivalence": "PASS",
  "privacy": "SANITIZED_NO_RAW_SESSION_IDENTIFIERS"
}
```

Populate counts from observed verified output rather than copying the example if they differ for a justified, investigated reason.

- [ ] **Step 7: Run final verification after receipt creation**

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_repo.py
git diff --check
python3 scripts/verify_repo.py
```

Expected: tests and verify PASS; privacy grep has no private runtime identifiers in committed production/docs/receipt material.

- [ ] **Step 8: Commit Task 6**

```bash
git add README.md tests/test_bootstrap_contract.py receipts/003-multi-session-corpus.public.json
git commit -m "docs: verify multi-session Session Search corpus (refs #7)"
```

---

## Final Integration Gate

- [ ] Run the full suite from a clean working tree:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_repo.py
git diff --check
git status --short
```

Expected: all tests PASS, `VERIFY PASS`, no diff-check errors, no unintended files.

- [ ] Inspect the complete branch diff against the approved design base:

```bash
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
```

Confirm no connector credentials, real capture content, private corpus paths, real session identifiers, or raw artifact hashes are committed.

- [ ] Update GitHub issue #7 with implementation evidence only after observable postconditions exist. Move Project #4 item to `In Progress / Running` when implementation starts, `Verifying` during private acceptance, and `Done / Accepted` only after final readback.

- [ ] Keep PR #8 as the design/spec lineage. Implementation should use a new branch/PR from the approved design commit so design review and executable changes remain independently auditable.

