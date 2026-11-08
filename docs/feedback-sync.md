# Agent feedback: operator runbook and portable synchronization

**Purpose.** Preserve what the *agent* learned while searching, then allow
another installation to reuse the same evidence. This is not a human ratings
widget, fine-tuning, or an online ranking-policy update.

The existing contracts are feedback-request.v0 → feedback-record.v0 →
retrieval-experience.v0. Implementation: session_search/feedback_inbox.py,
session_search/feedback_refresh.py and session_search/retrieval_experience.py.
Coordination: issues #215, #227, #229 and #230.

## Observed deployment boundaries (2026-11-08)

- ChatGPT → Arcade → Neon router → shards: the live read-only session_search
  returns a 32-hex request_ref per request (production deployment #8 at the
  observed checkpoint; reverify before relying on it).
- The App-backed plugin does not expose session_feedback: no durable
  service-side feedback writer is configured.
- The authorized exe.dev VM has a private local feedback inbox with one
  real agent feedback record from #229. That is *runtime data*, not a Git
  artifact or cross-host backup.
- A Neon feedback replica and recurring sync job **do not yet exist**.
  Serving search from Neon does not make Neon owner of feedback records.

## How the mechanism crosses trust boundaries

~~~text
agent request -> search result + opaque request_ref
              -> agent classifies issue / operator / outcome
              -> explicit feedback-request.v0
              -> private immutable feedback-record.v0
              -> portable export (private JSONL) -> authenticated transport
              -> other installation -> strict append-only import
              -> deterministic feedback refresh -> patterns
              -> NO automatic policy promotion
~~~

A feedback_ref identifies a concrete *feedback request*, not a corpus message.
An exact record replay is idempotent. Import preserves the original received_at
timestamp. A matching feedback_ref with *different* contents, including a
different received_at, is a conflict — never silently choose or overwrite.
Two installations can independently submit the same feedback request at
different times; such a conflict needs explicit evidence-based reconciliation.

The private exchange includes feedback notes. Unlike the public sanitized
experience/pattern projection, it is **not** safe for public Git, telemetry,
issue comments or chat. v0 forbids raw query/target fields, but free-text notes
must still be treated as sensitive.

## Capture a real agent observation

The agent uses a real request_ref returned by a live search. The outcome must
reflect observed evidence. A clean NO_SIGNAL does not prove historical absence
or a morphology defect.

Using an already-authorized private Linux shell:

~~~sh
umask 077
python3 -m session_search.feedback_inbox submit \
  --store-dir "$PRIVATE_FEEDBACK_INBOX" < "$PRIVATE_REQUEST_JSON"
python3 -m session_search.feedback_inbox refresh \
  --store-dir "$PRIVATE_FEEDBACK_INBOX" \
  --episodes "$PRIVATE_FEEDBACK_OUTPUT/experience.jsonl" \
  --patterns "$PRIVATE_FEEDBACK_OUTPUT/patterns.json"
~~~

Submit prints only the deterministic feedback_ref. Refresh prints counts and
policy_promotion=NOT_PERFORMED.

## Portable two-way exchange: installation A ↔ installation B

This slice supports two local inboxes on the same or different hosts.
**It does not communicate with the Neon database.** Transfer private JSONL
snapshots through an already-authorized, encrypted channel; never transfer
them in chat or the public repository.

On A, select a *fresh* path under a private (mode 0700) directory:

~~~sh
umask 077
python3 -m session_search.feedback_inbox export \
  --store-dir "$INBOX_A" --output "$PRIVATE_A/a-to-b.jsonl"
sha256sum "$PRIVATE_A/a-to-b.jsonl"
~~~

Securely copy the private snapshot to B. Verify the file digest matches,
then on B:

~~~sh
sha256sum "$PRIVATE_B/a-to-b.jsonl"
python3 -m session_search.feedback_inbox import \
  --source "$PRIVATE_B/a-to-b.jsonl" --store-dir "$INBOX_B"
~~~

Repeat B → A with a *fresh* snapshot filename. If no concurrent feedback
arrived, export both final inboxes to fresh private paths and compare
SHA-256 digests: deterministic ordering and JSON serialization give identical
bytes for identical record sets. CLI output includes counts, never raw notes.

The import checks **the entire input and all known conflicts before writing**.
Each record is then atomically published without overwrite and verified by
readback. An interrupted import can leave a valid *partial subset*: replaying
the same file safely fills the missing records, reporting duplicates as
unchanged. The two hosts do not share a distributed transaction.

Export refuses to overwrite an existing snapshot. Snapshot files are mode 0600
and local inbox directories mode 0700. Encryption in transit and backups
remain the transport operator's responsibility.

## Neon feedback adapter: explicit next boundary, NOT YET IMPLEMENTED

If Neon is selected as a feedback *replica*, it must consume and emit the
same validated feedback-record.v0 entries. The portable record contract must
not depend on Postgres, Neon Functions, cloud credentials or schema.

Minimum future adapter contract:

1. Conditional INSERT keyed by feedback_ref, **no upsert/overwrite**. Exact
   duplicate is a no-op; same ID with a different record is a conflict.
2. Bounded export/pages of full validated records, including original timestamp,
   with stable snapshot identity or resumable cursor.
3. Reconciliation compares **IDs and contents**, fills missing records in each
   direction, stops on conflicts and partial reads; verifies counts/digests.
4. Private least-privilege credentials, no private notes in public telemetry,
   and no additional database-admin privilege in the router.
5. Test actual Neon write/readback, replay, malicious/invalid record, conflict,
   interrupted transfer and local → Neon → local recovery.

These are **acceptance requirements**, not a claim that Neon sync exists.
Select ownership, backup retention and authorization at the separate
provider/storage boundary rather than making a database canonical by accident.

## Failure and recovery

- For PARTIAL serving, retain unavailable-shard evidence. A search miss over an
  incomplete corpus is UNKNOWN, not proof of absence.
- On import failure, preserve both inboxes and the snapshot, investigate
  privately, and never disable strict validation just to make it green.
- If the exe.dev VM is lost, local-only feedback may be lost. A Git PR or a
  pattern projection cannot reconstruct the original private feedback records.
- Feedback and conversation evidence have separate refresh lifecycles.
  Importing feedback never authorizes search-policy promotion.

QA: run the focused tests/test_feedback_exchange.py suite and then the
repository's canonical tools/dev/check. Only synthetic records enter tests/Git.
