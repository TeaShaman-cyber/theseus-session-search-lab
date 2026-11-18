# Local Session Read — implementation plan

> **For agentic workers:** Use a small RED→GREEN slice and normal repository QA.

**Goal:** From a Session Search result, let an agent read the existing SQLite session sequentially, without lexical search, using bounded pages.

**Architecture:** Add a focused stdlib-only Python read operation over existing `sessions` and `messages`. Its cursor binds session, position and best-effort local projection fingerprint. Never group repeated text or imply the source conversation is complete.

**Tech Stack:** Python >=3.11, sqlite3, unittest. No new packages.
**Spec:** [Issue #267](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/267) and its architecture comment.

## Constraints
- Read-only, no accepted-artifact or projection mutation.
- Preserve role, search_class, ordinal, message identity, coverage and all repeated occurrences.
- Bound both number of returned segments and total returned text; split oversized messages with explicit character offsets and a byte-based cursor so embedded NUL and multibyte UTF-8 survive. Return total byte length, not SQLite's NUL-truncating character length.
- A stale cursor fails closed. Projection fingerprint is a local best-effort check, not cryptographic proof or cross-process transaction pinning.
- No changes to search or cloud serving. No production deployment in this slice.

## First slice
1. Write tests in `tests/test_session_read.py` for duplicate-text messages, page continuation, complete-vs-partial coverage, stale/mismatched cursors, oversized-message segmentation, and absent session; verify RED.
2. Implement `session_search/session_read.py`: `read_session(corpus_root, session_id, *, limit=20, max_chars=16000, start_ordinal=0, cursor=None)` and a tiny `python -m session_search.session_read` CLI; verify focused GREEN.
3. Run `tools/dev/check`, inspect diff against exact base, commit one small reversible change, open Draft PR referencing #267; no merge until cloud parity has its own reviewed acceptance.

## Review focus
- Cursor copied from another session.
- Changes to the projection between calls.
- Empty or nonexistent session.
- Long single message larger than response budget.
- Same body in two distinct message identities.


## Local cursor conformance checkpoint — 2026-11-18

- Run deterministic 40+ message fixture with duplicate text, empty messages, UTF-8
  multibyte and embedded NUL content; page under three independent message/char
  budgets and reconstruct exact events with bounded, advancing continuation.
- Cursor replay is intentionally idempotent (read-only), not a remote ACK; a
  caller can retry a page safely. The test walker rejects repeated/nonadvancing
  continuations and checks that terminal indexed coverage is still labeled
  PARTIAL_SESSION_SLICE when the accepted artifact is partial.
- Version-3 cursor includes a BLAKE2s checksum to detect accidental token edits.
  **The checksum is not authentication**: local callers with source access can
  deliberately forge a valid token. Cloud generation/authority binding must be
  verified separately before exposing any remote reader.
- Reject malformed cursors, cross-session/corpus continuation, SQLite integer
  overflow and mid-read projection changes. Also test an atomic DB replacement
  *during connect*: capture projection_stamp before opening SQLite and compare
  it after opening and again after the read; never return old database content
  under a cursor stamped from the replacement. This is a best-effort file-change
  detector, not a stable snapshot of accepted evidence.
