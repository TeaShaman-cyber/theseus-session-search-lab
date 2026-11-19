# Agent feedback — two independent stores (issue #232)

**Current decision, 2026-11-08:** feedback is lightweight operational evidence,
not accepted corpus authority. Keep two feedback stores independent and review
them manually. No automated server-to-Neon synchronization, scheduler or
new JWT/auth service is needed.

## Private App readback checkpoint — 2026-11-19 (issue #272)

This project currently uses **one private App-backed Session Search consumer**,
not a public multi-tenant product. The existing `ssrouter2` deployment **#13**
and Arcade Gateway were independently verified with four tools:
`session_search`, `session_browse`, `session_feedback` and
`session_feedback_get`. A ChatGPT App-hosted exact-ref `session_feedback_get`
returned `FOUND` on three genuine records written in earlier chats. Database
and returned metadata matched; the read span was correlated to the original
search trace in Grafana Tempo using `feedback_ref` and `request_ref`.

At that checkpoint `session_feedback_get` intentionally returned
`notes_present` but **not** the stored `notes` text. Under the follow-up in
#272, the **Git source candidate** additionally returns the original nullable
`notes` (validated 1–4000 chars when present) for an exact 32-hex feedback ref
on the **same private authenticated App route**. Other feedback fields,
`notes_present`, immutable stored records, idempotent writes and existing
permissions remain unchanged. No list/export endpoint or ranking adaptation is
added. Never copy private notes, raw queries or source text into telemetry,
metric labels, logs or public issue receipts.

Source QA and a committed change are not evidence of production rollout. For
each subsequent promotion, verify the active Neon deployment, the unchanged
four-tool Gateway, current host discovery, a real read of a preexisting
record and installed App skill/source parity. Preserve the existing private
`.app.json` binding and historical dated checkpoints below.

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

## Historical router and Arcade activation acceptance — 2026-11-08

Native Neon deployed the source from exact main commit e9c5b23 to the
existing personal-org ssrouter2 Function with unchanged URI: active
deployment #9 COMPLETED. The original seven env key names were
preserved, and SESSION_SEARCH_FEEDBACK_DATABASE_URL was added,
pointing to the separate issue232 database through its restricted role.
The Arcade control plane added one discovered tool to the existing
Session Search gateway, which reports two exact tools:
TheseusSessionSearch.session_search and TheseusSessionSearch.session_feedback.

Fresh ChatGPT host discovery now selects both tools. A real
session_search request returned FOUND with all four expected shards,
unavailable_shards=[] and request_ref
72849be85a484a83869fa2e263e824c5. That retrieval was genuinely
low-relevance for the current issue-232 activation context, so the App
submitted truthful operational feedback through session_feedback rather
than a disposable synthetic record. The tool returned RECORDED with
feedback_ref 0a3b47e20852d6d45763e1d0e2dec60f.

Independent read-only Neon verification against project
plain-voice-04214202, branch br-dry-feather-bc0z04nc, database
feedback_issue232 found exactly one row with that feedback_ref in
session_feedback.records_v0. The record is retained as genuine retrieval
feedback. This verifies the ChatGPT host -> existing Arcade gateway ->
existing router -> dedicated Neon database write path end to end.

Current checkpoint: LIVE_APP_FEEDBACK_VERIFIED /
NEON_READBACK_VERIFIED / SEARCH_PASS / TWO_INDEPENDENT_STORES /
NO_AUTOMATIC_SYNC.
Server-local storage remains unchanged. No synchronization is scheduled.

The write/readback above is the #232 acceptance checkpoint. After the #264 grouped-default rollout, the production router advanced to deployment #10. Fresh 2026-11-10 Arcade discovery still exposed both `TheseusSessionSearch.session_search` and `TheseusSessionSearch.session_feedback`. No new feedback write was performed for that docs-currentness probe, so the #10 checkpoint proves tool discovery/availability, not a second end-to-end feedback write.

New Neon resource naming and cleanup rules are in docs/neon-housekeeping.md.
