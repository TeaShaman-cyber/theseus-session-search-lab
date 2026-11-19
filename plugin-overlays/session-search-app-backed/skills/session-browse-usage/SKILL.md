---
name: session-browse-usage
description: Use when the user needs to read, reconstruct, continue, or inspect an accepted historical conversation or branch beyond search snippets using the Theseus Session Search session_browse tool, or when diagnosing its cursor pagination or ChatGPT App discovery.
---

# Read a historical session through Session Search

This skill complements **session-search-usage**. Search finds occurrences; `session_browse` reads an **identified session/branch** in bounded, ordered pages. Both are read-only. Do not use a new repository, tool registration or database mutation for an ordinary browse.

## Request route and tool discovery

```text
ChatGPT web/mobile -> existing App-backed Session Search plugin
  -> Arcade_SelectTools / Arcade_UseTool
  -> TheseusSessionSearch.session_browse
  -> existing Session Search router -> exactly one pinned Neon shard
  -> session_search.read_generation_session_v1(...)
```

On ChatGPT, use `Arcade_SelectTools` to discover the **exact** named tool and its current input schema before invoking `Arcade_UseTool`. Do not invent a raw top-level tool or call an undiscovered tool. The same App/Gateway also retains `TheseusSessionSearch.session_search` and `TheseusSessionSearch.session_feedback`. The existing OAuth App binding is the intended web/mobile route; do not add or replace a packaged `mcp.json`/`.mcp.json` or direct shard connection.

If the gateway lists three tools but this host still discovers only the earlier two, report **STALE / REPROBE_REQUIRED at the host/tool-discovery boundary**; a refreshed conversation or reconnection may be necessary. A gateway control-plane update, a successful SQL call, or a healthy router does **not** prove that the new tool is invocable from this ChatGPT session. Do not re-register or rename a working server to bypass caching.

## When to browse

Use `session_search` first when the user gives a topic, quote, time, or vague recollection rather than a concrete session identity. Select the **actual occurrence** from returned `groups[].occurrences[]` (or the relevant representative only when it identifies the desired event). Carry through its exact `session_id`, `shard_id` and `generation_id`; `session_id` can designate a distinct branch. Never replace a branch-scoped ID with its parent, synthesize a generation, or pick a different shard with the same-looking text.

If the user already has an identified session, read directly: no new lexical search is needed. Use browse when a snippet does not establish the surrounding conversation, sequence of decisions, or later messages. Small focused search results may be sufficient without browsing.

## Input contract

**Required:** `session_id`, `shard_id`, `generation_id` from **one** returned occurrence. Do not omit an identity on continuation. **Optional:** `limit` (1..100, default 20), `max_chars` (1..100000, default 16000), `start_ordinal` (nonnegative first-page offset), or `cursor` (the opaque token from the preceding response). Never supply `cursor` and `start_ordinal` together.

Example, replace placeholders with returned source values (not guesses):

```json
{
  "session_id": "<occurrence.session_id>",
  "shard_id": "<occurrence.shard_id>",
  "generation_id": "<occurrence.generation_id>",
  "limit": 5,
  "max_chars": 4000
}
```

A subsequent page repeats those exact three identities and sends `"cursor": "<previous.next_cursor>"`; omit `start_ordinal`. The cursor is an opaque **position for read-only continuation**, not permission, source authority or a token to edit. Do not infer a cursor from `next_position` or from an ordinal. Prefer small pages on mobile and grow only when needed.

## Page assembly and stop conditions

For a successful response, check `status=OK` and the same session, shard, generation, plus `queried_shards` / `unavailable_shards`. Preserve `messages[]` **in returned order**. Messages can have equal bodies, equal `ordinal` with different `message_id`, and multi-page segments of a *single* message. Do not deduplicate by text, timestamp, or ordinal alone.

Each segment provides `ordinal`, `message_id`, `text`, `text_offset`, `text_total_chars`, `complete_message` and other provenance. Join continued text for the same `(ordinal, message_id)` in position order; verify `text_offset` equals the number of Unicode characters already collected for that message. Do not confuse Unicode character offsets with UTF-8 byte offsets. Empty messages are real events. Preserve role and any available search class/time metadata.

- If `has_more=true`, require a nonempty `next_cursor`; advance using it, checking it differs from earlier tokens. A retry of the **same** cursor may replay a page safely; don't append the same page twice after a retry.
- If `has_more=false`, require `next_cursor=null`: the **indexed accepted projection** is exhausted. It is **not** evidence that every message ever exchanged on the original platform was exported.
- If `status=BLOCKED`, `has_more` is unknown, or the tool reports `isError=true`, do not claim a complete result. Preserve accepted prior pages and any returned retry cursor and error reason; re-probe only the failing boundary. `unavailable_shards` and `queried_shards` are evidence, not absence of history.
- If a cursor is rejected as `STALE_GENERATION`, `CURSOR_SESSION_MISMATCH`, `CURSOR_SHARD_MISMATCH` or `CURSOR_INVALID`, stop rather than fabricating a cursor or silently switching to another generation. An invalid input may appear as a tool error instead of `status=BLOCKED`.

## Coverage and authority

`session_coverage` is the **accepted artifact/projection coverage**, not the entire raw ChatGPT account history. Keep these distinctions:

- `PARTIAL_SESSION_SLICE`: the exposed source slice is incomplete. Even a final page does not change that.
- `COMPLETE_EXPOSED_CONVERSATION`: the *accepted exposed* conversation is complete. `coverage_complete=true` **only** if this state is present **and** `has_more=false` on this page.
- `coverage_complete=false` on intermediate pages or partial histories; `has_more=false` and `coverage_complete=false` can be fully consistent.

A generation identifies a rebuildable Neon projection of accepted evidence; it is not raw-history authority. Never turn a clean `NO_SIGNAL`, a blocked page, or a completed partial projection into a claim that the original conversation never existed.

## Minimal host acceptance for the new tool

1. Inspect current Gateway `Session Search`: canonical remote server `theseus-session-search`, existing OAuth App binding, existing search/feedback tools plus new `session_browse` tool. See `session-search-usage/references/gateway.md` for the existing connection boundary. Do not create a new gateway.
2. In a fresh ChatGPT App connection use `Arcade_SelectTools` to observe `TheseusSessionSearch.session_browse` and inspect its input schema. `tools_count=3` in the Arcade management plane is insufficient without host discovery.
3. Search a known safe term via `session_search`, choose an actual ChatGPT occurrence with pinned IDs, and browse with a small `limit`/`max_chars`. Check identities, ordered segments, page continuation and partial coverage.
4. Follow `next_cursor` into a second page; optionally retry the same input cursor to check idempotent replay. Stop on `has_more=false` or `BLOCKED`, preserving prior evidence. Verify the old `session_search` tool still works.
5. Mark `HOST_VERIFIED` **only** after a real host `Arcade_UseTool` call to `session_browse` succeeds with identity/provenance and pagination checks. Until then report `HOST_STALE` / `NOT_VERIFIED`; do not claim that Neon/Arcade gateway deployment alone finished host acceptance.

## Privacy and scope

Do not publish actual retrieved conversation bodies, credentials, private connection strings, OAuth tokens or authorization headers in public GitHub issues, receipts or diagnostics. The consumer plugin does not authorize corpus mutation, generation activation, unrelated writes, or privilege changes. Use separate operational workflows for those actions; this skill is only for reading accepted history.
