# Neon housekeeping — 2026-11-08 verified inventory

**Scope:** Theseus Session Search derived Neon infrastructure. This is a
dated **read-only discovery snapshot**, not authority to remove a resource.
The live Neon inventory, App-backed search, current router catalog, accepted
source releases, GitHub Issues, and role/Function readback must be rechecked
before any destructive action. Coordination: issue #235. Feedback-specific
adapter: issue #232. Source authority is the versioned accepted corpus, not
Neon and not this inventory.

## What we actually have

Two **distinct access scopes**, not a requirement for two future stores:

- Personal Neon organization: eight projects. The current production router
  Function ssrouter2 is on project royal-cake-29698192, branch
  br-patient-union-b5xe6jip, deployment #8. A separate personal project
  misty-darkness-46441019 hosts multiple shard Functions and cannot be treated
  as expendable merely because its project name mentions ChatGPT.
- Dedicated Theseus Session Search Neon organization: originally eight
  projects before the authorized November 8 cleanup; now **six active
  projects**. Four hold the current serving generations and two retain
  previous ChatGPT generations for rollback.

**Baseline inventory:** 16 visible projects, 39 visible branches (including
default) before deletion. **Post-cleanup:** 14 active projects across both
organizations, 37 active branches. The two deleted single-branch projects
remain listed below as historical evidence and are recoverable in Neon's
deletion window at the observed postcondition. These counts are **not**
a current price or billed-size estimate. Cloned branches share storage; logical_size values cannot be summed
as cleanup savings. The direct App-backed Session Search read-only probe
on 2026-11-08 returned FOUND, four queried shards
(chatgpt-0001, chatgpt-0002, deepseek-0001, xai-0001),
unavailable_shards=[].

Classification:
- KEEP: currently active/required serving or independent project owner.
- ROLLBACK: a previously accepted recovery/legacy asset; retain until release
  contract #166 explicitly discharges its rollback obligations.
- REVIEW_DELETE: plausible obsolete test/duplicate with verified history,
  still requires live dependency check and explicit deletion approval.
- UNKNOWN: precise dependency or safe-retention window not yet established.
- REMOVED: already eliminated, independently verified.

## Dedicated data organization — projects and branches

| Project / current branch identity | Purpose / evidence | State |
| --- | --- | --- |
| old-smoke-74649402, main br-mute-paper-bdyf171i | Blue ChatGPT-0001 from #147; ACTIVE generation starts 3396ab63553e; ssshard deployment #3; current App returned same generation prefix. | KEEP |
| purple-cell-65542575, main br-cold-cake-bd2rp458 | Blue ChatGPT-0002 from #147; ACTIVE starts d8604fd227d8; ssshard #3. | KEEP |
| morning-tooth-71193579, main br-withered-snow-b5amv40x | DeepSeek-0001, ACTIVE starts 5fef1c896745; live #166 release vector. | KEEP |
| wild-surf-61625738, main br-floral-morning-b5kactgx | xAI-0001, ACTIVE starts 6095e643b3fc; live #166 vector. | KEEP |
| super-resonance-00490441, former main br-silent-river-bdmqdj1n | Unselected ss147 ChatGPT-0001 duplicate. Same generation/manifest/schema/counts as blue; no Functions/domains; approved and deleted November 8. | REMOVED (recoverable at verification) |
| falling-bread-64015288, former main br-super-butterfly-begln6qu | Unselected ss147 ChatGPT-0002 duplicate. Same generation/manifest/schema/counts as blue; no Functions/domains; approved and deleted November 8. | REMOVED (recoverable at verification) |
| spring-glade-75307688, main br-bitter-sea-b45yqusy | Older pre-ss147 ChatGPT-0001 projection; ACTIVE c75478c3..., not current App generation. Retained for rollback by #147. | ROLLBACK |
| plain-wave-74064803, main br-late-lake-b51uohkq | Older pre-ss147 ChatGPT-0002 projection; ACTIVE bce15b5b..., not current App generation. | ROLLBACK |

Additional non-default branches (do **not** confuse with separate projects):

| Project | Branch name / ID | State |
| --- | --- | --- |
| old-smoke-74649402 | issue-188-topk-eval-20261107 / br-rough-queen-bdbvmfn0 | REVIEW_DELETE after #188 fixture/candidate-readback audit; cloned shard Functions are present |
| spring-glade-75307688 | issue137-retrieval-policy-20261101 / br-floral-boat-b49nvnr4 | REVIEW_DELETE after rollback/candidate consumers checked |
| plain-wave-74064803 | issue137-retrieval-policy-20261101 / br-wandering-snow-b5b0gb9f | REVIEW_DELETE after rollback/candidate consumers checked |
| morning-tooth-71193579 | issue137-retrieval-policy-20261101 / br-spring-queen-b5paz3tw | REVIEW_DELETE after consumer audit |
| wild-surf-61625738 | issue137-retrieval-policy-20261101 / br-broad-wildflower-b5vcg9mu | REVIEW_DELETE after consumer audit |
| wild-surf-61625738 | issue86-conflict-replay-20261031 / br-shiny-forest-b58tar4p | REVIEW_DELETE after #86 retry/fixture audit |
| wild-surf-61625738 | issue-116-lakebase-bm25-ab-20261031 / br-sweet-dream-b5qzaztb | REVIEW_DELETE after #116/#188 benchmark artifact coverage |

## Personal organization — projects and branches

| Project | Observed purpose / scope | State |
| --- | --- | --- |
| royal-cake-29698192 | Current registered production router in br-patient-union-b5xe6jip; original main also has ssrouter2. Preserve production endpoint, credentials, catalog and rollback. | KEEP (project) |
| misty-darkness-46441019 | Legacy/transition shard Function host: multiple ssshard Functions across 10 branches. May still service DeepSeek/xAI; names are misleading. Need exact live catalog target map. | KEEP/UNKNOWN (project) |
| ancient-bonus-25507065 | Archived duplicate DeepSeek arcade-building project; single default br-floral-cherry-b4x357cc, no returned Functions. | REVIEW_DELETE after role/dependency audit |
| royal-wind-39888328 | Archived xAI-0001-v2 project; br-hidden-bonus-b4jntyc6, no returned Functions. | REVIEW_DELETE after role/dependency audit |
| dry-poetry-12579932 | Archived xAI-0001 project; br-dawn-queen-b4yy6apb, no returned Functions. | REVIEW_DELETE after role/dependency audit |
| ancient-morning-06623139 | Archived ss64 Hermes-B historical project; br-spring-sound-b44hg0v8 retains ssshard Function deployment #5 (completed), even though the branch is archived. | UNKNOWN: require exact #64/Hermes consumer and endpoint audit |
| flat-resonance-49725344 | Separate Theseus research development sandbox; default br-rough-math-aeencra7; not Session Search cleanup by inference. | KEEP (other owner) |
| billowing-butterfly-30206276 | Vercel CI watcher canary; main br-tiny-wave-ar8u4ovq and vercel-dev br-delicate-hill-arkjnpns; outside Session Search ownership. | KEEP (other owner) |

Router project royal-cake-29698192 branches:

| Branch / ID | Purpose | State |
| --- | --- | --- |
| issue137-retrieval-policy-router-20261101 / br-patient-union-b5xe6jip | **Production** ssrouter2 deployment #8. | KEEP |
| main / br-soft-base-b55ple0i | Original project default and ssrouter2; possible rollback asset. | ROLLBACK |
| ss166-s2-pinned-canary-20261106 / br-quiet-term-b5uqmx74 | Isolated pinned serving canary #166; ssrouter2 deployment #3. | REVIEW_DELETE only after #166 closes S2 rollback |
| chatgpt-router-candidate-20261031 / br-old-union-b5qyign2 | #147 candidate ssrouter147 and earlier ssrouter2. | REVIEW_DELETE after current catalog / old-router rollback audit |
| issue-116-env-only-deploy-canary-20261031 / br-divine-glitter-b5tl65gc | #116 Function environment canary. | REVIEW_DELETE |
| canary-xai-deepseek-20261025 / br-restless-hall-b5kaph3r | Initial router fan-out canary. | REVIEW_DELETE |
| xai-candidate / br-dark-night-b5hku3bl | Early xAI router candidate. | REVIEW_DELETE |

Shard Function host misty-darkness-46441019 (10 branches total):

| Branch / ID | Why retained or candidate |
| --- | --- |
| main / br-raspy-pine-b4y8bsyq | Default legacy shard Function; ROLLBACK pending exact serving URL map |
| xai-candidate / br-young-art-b47cvyjd | Candidate ssshard; REVIEW_DELETE after URL map |
| ss147-chatgpt0001-serving-20261104 / br-lingering-silence-b41yl5e3 | #147 shard Function; UNKNOWN until live URL/cutover map |
| issue137-chatgpt0002-serving-20261101 / br-hidden-pond-b4wro919 | Transitional ssshard; UNKNOWN |
| issue137-xai0001-serving-20261101 / br-crimson-star-b4dhtud5 | Potentially live xAI Function; KEEP until verified |
| issue137-chatgpt0001-serving-20261101 / br-soft-leaf-b4rv9zgu | Transitional ssshard; UNKNOWN |
| issue137-deepseek0001-serving-20261101 / br-old-mouse-b4962ahn | Potentially live DeepSeek Function; KEEP until verified |
| chatgpt-0002-serving-20261031 / br-wandering-mountain-b41qo8k1 | Historical ssshard; REVIEW_DELETE after URL map |
| chatgpt-0001-serving-20261031 / br-withered-lab-b4j729ow | Historical ssshard; REVIEW_DELETE after URL map |
| canary-deepseek-0001-20261025 / br-morning-surf-b4au9u4j | Older ssshard canary; REVIEW_DELETE after URL map |

## What can be cleaned first — bounded order

1. **Already removed:** mistaken feedback records_v0 table and its empty
   session_feedback schema from personal production-router neondb, after exact
   zero-row/no-extra-objects guard. Independent Neon readback:
   schema_absent=true, table_absent=true, schema_objects=0. The local genuine
   feedback record on exe.dev is unaffected. A versioned optional migration
   file in Git is **not** proof the table remains deployed.
2. **Completed first deletion pair:** super-resonance-00490441 and
   falling-bread-64015288, following explicit user authorization,
   duplicate-record/Function/dependency checks and sequential App-backed
   smoke after each deletion. Details and remaining uncertainty below.

### First duplicate-pair validation and deletion — 2026-11-08

Current independent read-only Neon SQL comparisons confirmed that the two
unselected ss147 projects (super-resonance-00490441 and
falling-bread-64015288) match the chosen blue shard pair in every compared
generation metadata field: generation ID, shard/provider, source manifest
digest, projection schema version, READY state, and exact session and message
counts. The first pair each contains **254 sessions / 67,293 messages**;
the second **232 sessions / 46,052 messages**. No private source digests
or content were published.

Each unselected project has exactly one default branch, zero returned Neon
Functions, zero custom Function domains and one neondb. Organization-wide
GitHub code search found zero references to their two project IDs and branch
IDs; this is a limited source-control check, **not** an exhaustive consumer
inventory or a proof of no hidden runtime configuration.

A fresh App-backed control query returned FOUND, four queried and zero
unavailable shards. The production router Function has a completed deployment
and a SESSION_SEARCH_CATALOG environment key, but the native Function
reader returns environment variable names only, not their current values;
the **exact current catalog endpoint-to-project mapping remains unverified**.
Issue #147 records selection of the blue pair at cutover; this is historical
evidence, not the current literal endpoint list.

**Authorized cleanup execution:** the user explicitly authorized deleting
these two named projects after the #235 review. Immediately before deletion,
native Neon reconfirmed their exact IDs, one non-protected branch each, no
Functions/domains, and working blue Functions on the selected projects.
The historical #147/#166 receipts bound production to the selected blue
pair; a current App-backed baseline returned FOUND on both refreshed ChatGPT
generations, four queried shards and no unavailable shards. Neon deleted
super-resonance-00490441 first. Independent project listing showed it
absent from active projects and present among recoverable projects; a fresh
App-backed search still returned FOUND with four available shards. Then
Neon deleted falling-bread-64015288; independent listing showed **both**
absent from active projects, both recoverable, and the two selected blue
projects still present. A second fresh App-backed control again returned
FOUND with both expected ChatGPT generation prefixes, four queried shards,
and zero unavailable shards.

**Residual uncertainty:** the literal current SESSION_SEARCH_CATALOG URL
mapping remained masked by Neon's Function-reader interface, so independent
proof of every external/third-party consumer is **UNKNOWN**. The action was
bounded to the exact user-authorized two projects, backed by source/receipt
evidence, endpoint/Function inventory, provider recoverability, and live
post-delete verification. Do not infer that deletion of another candidate
has passed these same gates. No cost saving has been calculated.

3. **Contained experiments:** issue-188 evaluation branch, issue-116 env canary,
   old October canaries and obsolete issue137/issue86 branches. A completed
   issue/test is not itself proof that no URL, job, or rollback depends on it.
4. **Archived legacy projects:** personal xAI / DeepSeek / Hermes prototypes.
   Lower immediate priority; verify cross-project ownership and retention.
5. **Preserve:** live four shard generations, all serving Functions, production
   router and associated credentials, accepted corpus/release authority, and
   old rollback projects until #166 formally discharges the rollback window.

**Deletion gate for each object:** observed exact ID + current scope, no active
catalog endpoint or connected workflow, retained authoritative rebuild source
and tested recovery, acceptable rollback/retention, explicit deletion scope,
then independent project/branch/Function readback. No cascade, bulk delete,
or cost-saving claim merely from logical_size.

## Evidence pointers and unresolved checks

- Issue #147 (November 4 blue/green cutover and duplicated original pair):
  https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/147
- Issue #166 (November 6 S2 pinned router, S3/S4 and rollback still open):
  https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/166
- Issues #86 (retry), #116 (Neon canary), #137 (retrieval policy),
  #188 (search experiments), #150/#171 (operations/cost).
- Session Search historical retrieval on 2026-11-08 showed mixed
  PARTIAL_SESSION_SLICE and older generic hits; treat those as historical
  context, not fresh deletion proof. Exact recent project-resource mapping
  is from native Neon reads and GitHub #147/#166. No history absence inferred.
- Native Neon confirmed the personal and data project sets; exe.dev provider
  proxy sees only data organization. Different access scopes are not extra
  application architecture requirements.
- **Still UNKNOWN:** literal current router catalog Function URLs for all
  shards, separately authorized external recovery consumers, billed savings
  and precise post-#166 retention gate. The completed first pair is
  verified from active inventory and healthy post-delete consumer search,
  not a claimed full URL/config disclosure. Resolve only these to approve the first
  deletion batch, rather than creating another benchmark.
