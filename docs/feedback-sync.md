# Agent feedback — two independent stores (issue #232)

**Current decision, 2026-11-08:** feedback is lightweight operational evidence,
not accepted corpus authority. Keep two feedback stores independent and review
them manually. No automated server-to-Neon synchronization, scheduler or
new JWT/auth service is needed.

## Stores and failure boundary

**Server-side agent:** existing private local feedback inbox, stored near the
agent runtime. The single real feedback-record.v0 already present stays there.
Loss of an ephemeral runtime may lose feedback; for now we accept this risk.

**ChatGPT App:** existing App-backed Session Search router conditionally exposes
session_feedback alongside session_search. The writer stores immutable
feedback-record.v0 only in dedicated Neon PostgreSQL database feedback_issue232
in project theseus-ss-feedback-issue232. It never writes to corpus shard or
production router tables. These stores use the same portable record schema,
but their state is not replicated.

## Manual processing

For an authorized feedback review, collect records independently from the
server's local inbox and the App's feedback_issue232 database using existing
read permissions. Preserve the source of each record, feedback_ref and
received_at; reconcile conflicts with evidence, never by silent overwrite.
Process the selected private combined set with the existing deterministic
feedback_refresh and retrieval_experience code. Findings are reviewed manually,
not automatically promoted into search policy.

The older JSONL exchange and feedback_neon two-way adapter remain in Git
but are not part of the active design. Do not schedule them or automatically
copy records between the server and App.

## App writer contract and implementation status

The existing router accepts POST /mcp with its existing bearer authentication.
The bounded session_feedback input is: baseline_request_ref, optional
result_request_ref, failure_class, operator name/version, outcome and optional
notes. Raw query or target fields are rejected. It derives exactly the same
feedback_ref as the local record contract and stores a UTC received_at.
An exact replay returns UNCHANGED; a conflicting stored record fails.
No UPDATE/DELETE and no raw feedback or SQL error logging.

A separate router environment setting SESSION_SEARCH_FEEDBACK_DATABASE_URL
enables this writer only when it names the exact database feedback_issue232.
The corpus search route remains unchanged; absent this setting the App
continues to expose only session_search.

Neon resource state independently observed:
- project: theseus-ss-feedback-issue232 / plain-voice-04214202;
- branch: main / br-dry-feather-bc0z04nc;
- database: feedback_issue232; table: session_feedback.records_v0;
- limited existing role: feedback_sync_issue232 (legacy setup name),
  SELECT/INSERT true and UPDATE/DELETE false;
- initial record count: zero.

The database/schema and code are separate from production activation. The
existing router is in a different Neon organization than the new database,
so a writer connection setting must be applied through an authorized route
that preserves all existing router deployment environment variables.
Do not pass credentials via chat, Git or issues, or overwrite production
router auth/catalog settings. A green CI test does NOT mean the App write tool
is live. Require a real App tool listing and successful feedback write/readback
before claiming it.

## Router and Arcade activation checkpoint — 2026-11-08

Native Neon deployed the source from exact main commit e9c5b23 to the
existing personal-org ssrouter2 Function with unchanged URI: active
deployment #9 COMPLETED. The original seven env key names were
preserved, and SESSION_SEARCH_FEEDBACK_DATABASE_URL was added,
pointing to the separate issue232 database through its restricted role.
The Arcade control plane added one discovered tool to the existing
Session Search gateway, which now reports two exact tools:
TheseusSessionSearch.session_search and TheseusSessionSearch.session_feedback.
The App-backed search path after rollout returned FOUND, four
queried shards, no unavailable shards and unchanged ChatGPT generation
prefixes.

**Host-level write acceptance VERIFIED on 2026-11-08.**
In this existing ChatGPT conversation, Arcade_SelectTools initially
returned only search after the gateway mutation. A later fresh query
through the SAME App surfaced both
TheseusSessionSearch.session_search and
TheseusSessionSearch.session_feedback without creating another App,
gateway, server, authorization system or OAuth connection.

The App-backed session_feedback tool accepted one **synthetic-only**
issue232 canary (baseline c repeated 32 times, failure_class
issue232_canary), returned RECORDED with immutable feedback_ref
7fd2c2605f0ec038d6bae3ef5ceb8596. Independent read-only Neon SQL
confirmed that exact record, schema and timestamp in feedback_issue232.
Replaying the identical App tool call returned UNCHANGED with the
same feedback_ref. A narrowly scoped Neon DELETE removed the single
synthetic record; independent SQL readback verified 0 canary rows
and 0 total rows. A fresh App-backed search *after* cleanup returned
FOUND, all four current shards, unavailable_shards=[] and unchanged
pinned ChatGPT generations.

Current checkpoint: ROUTER_DEPLOYED / GATEWAY_TWO_TOOLS_VERIFIED /
APP_WRITE_READBACK_VERIFIED / REPLAY_IDEMPOTENT_VERIFIED /
SYNTHETIC_TEST_CLEANED / SEARCH_PASS.
Server-local storage remains unchanged. No synchronization is scheduled.

New Neon resource naming and cleanup rules are in docs/neon-housekeeping.md.
