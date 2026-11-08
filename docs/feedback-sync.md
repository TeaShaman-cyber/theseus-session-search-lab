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

## Optional Neon PostgreSQL replica: schema + operator adapter

The project-owned portable feedback record contract does **not** depend on Neon.
Issue #232 adds only a replaceable PostgreSQL replica adapter, without a new
service dependency or model-training loop. The database is a *copy*, not the
canonical owner of feedback.

**Before enabling:** select a specifically authorized Neon database and scoped
PostgreSQL role. The initial target is the empty neondb in the existing private
router-project branch, isolated under schema session_feedback; verify the live
branch/project and table inventory before applying any SQL. Do not move raw
corpus data, personal connection strings or secret keys through ChatGPT.

Apply the reviewed migration once as that database's owner:

    psql -X -v ON_ERROR_STOP=1 -f sql/neon/010_feedback_replica_v0.sql

It creates session_feedback.records_v0, keyed by immutable feedback_ref with a
shape CHECK. No corpus table is touched. PUBLIC schema/table privileges are
revoked. A future connection role must receive only schema USAGE and table
SELECT+INSERT, not UPDATE/DELETE or database-admin. This is a protected
permission boundary: do not silently create a privileged role.

With an existing *authorized* libpq connection on the agent host (PGSERVICE,
libpq environment or local keyring; no URL/password in CLI arguments), perform
one explicit bounded two-way reconciliation:

    python3 -m session_search.feedback_neon \
      --store-dir "$PRIVATE_FEEDBACK_INBOX" \
      --expected-database neondb

The command validates every local/remote feedback-record.v0 before writing,
checks same-key content equality, uploads only absent records in one transaction,
verifies remote readback, imports new remote records by atomic no-overwrite
local write, then compares both inventories again. Replays are idempotent.
On interrupted operations, retry after checking status; no delete/rewrite
shortcut. It caps remote reads at 1000 records and fails if this limit is
exceeded rather than silently accepting an incomplete view; pagination is
future work only when capacity demands it. Output contains counts only.

**Verified operating state (2026-11-08, issue #232):** the reviewed
migration was applied to the private personal-organization router project's
existing neondb. Independent Neon readback confirmed schema
session_feedback, table records_v0, the feedback_ref primary key and
feedback-record shape CHECK. PUBLIC table SELECT and schema USAGE are denied.
A temporary same-transaction PostgreSQL canary verified exact replay,
conflicting-payload non-overwrite and invalid-shape rejection. Temporary
test data was not retained; the persistent replica still has **0 records**.
The serving router remained on deployment #8.

**Current route check (2026-11-08, read-only):** the Neon connector on
exe.dev **is working**, and the previously accepted provider-native SQL route
remains the intended operator path:
exe.dev -> neon.int.exe.xyz -> connection_uri -> psql -f.
See TeaShaman-cyber/theseus-cloud-shell issue #10 and
providers/exe-dev/README.md; a historical read-only psql file execution
was independently verified. Do **not** classify this connector as broken.

The currently attached provider identity lists eight Neon projects in the
*Theseus Session Search* organization. These are existing provider/shard
projection projects, including blue/candidate branches; **none is identified
as a dedicated private feedback store**. The proxy's GET for the existing
personal-organization router project returned HTTP 404, both by project ID
and by explicitly requested personal-organization scope. This is a
**scope/identity mismatch**, not a general inability to perform SQL.
Native ChatGPT Neon access to that personal-organization project is a
different authenticated boundary, and is not a bridge for guest credentials.

**BLOCKED, specific postcondition:** the first real feedback record still
resides only in the exe.dev local inbox; the personal-organization Neon table
remains at 0 records and the end-to-end sync has not been verified. Do not
move private feedback into a corpus shard or silently change store ownership.

**One decision remains:** grant the already attached provider integration
appropriately scoped access to the *existing* private replica, or explicitly
select a dedicated feedback store in the data organization. Either changes
permission or storage authority and needs its own approval and current
readback. Do not create a new key, account, database or adapter by inference.
Once an authorized end-to-end connection is available, run the versioned
session_search.feedback_neon adapter, verify exact record readback,
idempotence and conflict handling, and only then declare synchronization live.
Direct ChatGPT session_feedback and recurring automation remain separate.


### Remote acceptance before calling synchronization live

Observe an actual authorized connection, schema/table readback, synthetic
insert→exact replay→conflict rejection, remote→local→remote reconciliation
with private record bytes, error handling and a recovery retry. Keep native
Neon-provider and shell executor evidence separate. Do not claim a live sync
while one of these surfaces is blocked.

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
