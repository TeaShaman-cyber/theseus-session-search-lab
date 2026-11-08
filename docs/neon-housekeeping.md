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
default) before deletion. **After the first project batch:** 14 active projects, 37 active branches.
**After the second branch batch (2026-11-08):** 14 active projects,
32 active branches. Five test-only data branches were removed. **After
the third router-canary batch (2026-11-08):** 14 active projects and
**31 active branches**. One independent router leaf branch was removed;
the remaining three old router candidates are ancestors of production
and are not deletable in-place with Neon branch-tree constraints.
**After the fourth shard-leaf batch (2026-11-08):** **14 active projects,
27 active branches**. Four accepted issue137 serving test branches have
been removed; DeepSeek/xAI live Function branches remain.
**After the fifth isolated ss147 Function-leaf cleanup (2026-11-08):
14 projects and 26 active branches.** The two deleted single-branch projects
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
| old-smoke-74649402 | issue-188-topk-eval-20261107 / br-rough-queen-bdbvmfn0 | KEEP / RECHECK LATER: #188 open; two cloned Functions present |
| spring-glade-75307688 | issue137-retrieval-policy-20261101 / br-floral-boat-b49nvnr4 | REMOVED (historical reference) |
| plain-wave-74064803 | issue137-retrieval-policy-20261101 / br-wandering-snow-b5b0gb9f | REMOVED (historical reference) |
| morning-tooth-71193579 | issue137-retrieval-policy-20261101 / br-spring-queen-b5paz3tw | REMOVED (historical reference) |
| wild-surf-61625738 | issue137-retrieval-policy-20261101 / br-broad-wildflower-b5vcg9mu | REMOVED (historical reference) |
| wild-surf-61625738 | issue86-conflict-replay-20261031 / br-shiny-forest-b58tar4p | REMOVED (historical reference) |
| wild-surf-61625738 | issue-116-lakebase-bm25-ab-20261031 / br-sweet-dream-b5qzaztb | KEEP / RECHECK LATER: private gold table and BM25 index retained; #116 evidence |

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
| chatgpt-router-candidate-20261031 / br-old-union-b5qyign2 | #147 candidate; parent of CURRENT production branch, not deletable as a leaf. | BLOCKED_BY_CHILD_CHAIN |
| issue-116-env-only-deploy-canary-20261031 / br-divine-glitter-b5tl65gc | #116 envcanary116 + ssrouter2; parent of #147 candidate, hence of production; attempted deletion correctly rejected by Neon 422. | BLOCKED_BY_CHILD_CHAIN |
| canary-xai-deepseek-20261025 / br-restless-hall-b5kaph3r | Initial router fan-out leaf; deleted after exact branch/Function checks, verified gone from active branch list. | REMOVED (historical reference) |
| xai-candidate / br-dark-night-b5hku3bl | Ancestor of #116, #147, production and #166; cannot be deleted as a leaf. | BLOCKED_BY_CHILD_CHAIN |

Shard Function host misty-darkness-46441019 (10 original branches, 5 active after fifth batch):

| Branch / ID | Why retained or candidate |
| --- | --- |
| main / br-raspy-pine-b4y8bsyq | Default legacy shard Function; ROLLBACK pending exact serving URL map |
| xai-candidate / br-young-art-b47cvyjd | Live xAI Function endpoint established in #126/#166; preserve for serving/rollback. KEEP |
| ss147-chatgpt0001-serving-20261104 / br-lingering-silence-b41yl5e3 | Isolated #147 trial ssshard Function #7, separate from active blue Function; REMOVED 2026-11-08. |
| issue137-chatgpt0002-serving-20261101 / br-hidden-pond-b4wro919 | Accepted #137 diagnostic leaf; REMOVED 2026-11-08. |
| issue137-xai0001-serving-20261101 / br-crimson-star-b4dhtud5 | Accepted #137 diagnostic leaf, not #166 live parent; REMOVED 2026-11-08. |
| issue137-chatgpt0001-serving-20261101 / br-soft-leaf-b4rv9zgu | Accepted #137 diagnostic leaf; REMOVED 2026-11-08. |
| issue137-deepseek0001-serving-20261101 / br-old-mouse-b4962ahn | Accepted #137 diagnostic leaf, not #166 live parent; REMOVED 2026-11-08. |
| chatgpt-0002-serving-20261031 / br-wandering-mountain-b41qo8k1 | Previous ChatGPT-serving Function #2; retain as rollback candidate under open #166 (exact rollback path not retested). |
| chatgpt-0001-serving-20261031 / br-withered-lab-b4j729ow | Previous ChatGPT-serving Function #2; retain as rollback candidate under open #166 (exact rollback path not retested). |
| canary-deepseek-0001-20261025 / br-morning-surf-b4au9u4j | Live DeepSeek Function endpoint established in #126/#166; KEEP despite misleading name. |

## Fifth batch — ss147 isolated ChatGPT Function leaf, 2026-11-08

Scoped follow-up to closed #147 and ongoing rollback #166 in the old
shard Function-host project misty-darkness-46441019.

**Evidence before removal:**
- Selected live ChatGPT-0001 Function is project old-smoke-74649402
  on br-mute-paper-bdyf171i, ssshard deployment #3. Live App
  LIGHT_SONAR strict control returned FOUND, four queried and
  zero unavailable; the current blue ChatGPT generation was observed.
  #147's accepted cutover receipts independently identify the blue pair.
- The candidate br-lingering-silence-b41yl5e3 was a distinct legacy-host
  URL/branch, non-default and unprotected, with zero child branches,
  a single ssshard deployment #7, zero Function custom domains,
  and no branch-ID references from the bounded GitHub code search.
- The two older October 31 ChatGPT-serving Function branches
  br-wandering-mountain-b41qo8k1 and br-withered-lab-b4j729ow
  have different histories and were **not** classified as disposable:
  #147 retained previous generations for rollback, and #166 has not
  completed the rollback/deletion acceptance.

**Action and postcondition:** user-directed cleanup deleted only
br-lingering-silence-b41yl5e3. Native Neon delete_branch returned
success and a fresh active-only branch list omitted it while retaining
the five expected branches: main, DeepSeek serving, xAI serving,
and both old October ChatGPT serving branches. An independent fresh
App-backed strict control again returned FOUND, all four shards queried,
zero unavailable, unchanged blue ChatGPT generation prefixes.
No router, data generation, permissions, source, or rollback Function
was modified. No billing savings claim.

**Disposition:** the old shard Function-host project now has five
active branches, not ten. This fifth batch deletes a single leaf and
explicitly **retains** the two October rollback candidates. Further
deletion of the rollback pair requires #166's actual rollback/drain
disposition rather than a new speculative dependency investigation.

## Fourth batch — issue137 transitional shard leaves, 2026-11-08

The completed retrieval-policy work in GitHub issue #137 already records
its strict/recall/auto App-backed acceptance; production now runs the
separately pinned #166 shard release vector. Fresh #166 and #126 receipts
identify the actual live DeepSeek/xAI Function **parents** on
br-morning-surf-b4au9u4j and br-young-art-b47cvyjd, which were retained.
Current Neon checks verified all four old issue137 *children* below
were unprotected non-default leaves, each with a completed historical
ssshard deployment #1 and **no** custom Function domains. A bounded
GitHub code search found zero references to their exact branch IDs.

The following old **test Function branches** were explicitly cleaned
from project misty-darkness-46441019, without touching parent branches:

- br-hidden-pond-b4wro919 — issue137-chatgpt0002-serving.
- br-soft-leaf-b4rv9zgu — issue137-chatgpt0001-serving.
- br-crimson-star-b4dhtud5 — issue137-xai0001-serving.
- br-old-mouse-b4962ahn — issue137-deepseek0001-serving.

Each Neon delete_branch succeeded. Independent active-only list_branches
immediately after each showed the target absent and the default main,
actual DeepSeek and xAI serving branches still present. A fresh
App-backed real Session Search probe **between the two ChatGPT deletes
and the two DeepSeek/xAI deletes**, and again after all four, returned
FOUND, all four queried, unavailable_shards=[], same two pinned
ChatGPT generation prefixes. Another read-only Neon inventory confirmed
six remaining active branches of this historical Function-host project.

**Retained (not part of this batch):** main, xai-candidate live xAI,
canary-deepseek live DeepSeek, two older Oct31 ChatGPT serving branches
and (at that checkpoint) ss147-chatgpt0001-serving with separate
rollout histories. The latter was subsequently removed by the
independently verified fifth batch below. No billed savings
or new provider/permission actions claimed.

## Router canary cleanup — November 8 third batch

The existing router project is a **lineage**, not four independent
disposable branches. Fresh Neon read-only parent_id inventory:

    main br-soft-base-b55ple0i (keep)
      -> xai-candidate br-dark-night-b5hku3bl
         -> issue116-env-canary br-divine-glitter-b5tl65gc
            -> chatgpt-router-candidate br-old-union-b5qyign2
               -> PRODUCTION br-patient-union-b5xe6jip (keep)
                  -> ss166-pinned-canary br-quiet-term-b5uqmx74 (keep)
         -> canary-xai-deepseek br-restless-hall-b5kaph3r (REMOVED)

The user approved cleaning old router-canaries; preflight located
the correct personal router project and current production
ssrouter2 deployment #8. Historical closed #116 already documents
env-only deployment inheritance and executable marker readback. Current
Neon Function inventory confirmed the #116 branch contains
envcanary116 #2 and ssrouter2 #2; October canary leaf contained
ssrouter2 #1; xai-candidate still hosts ssrouter2 #12. None of the
three old canary branches had a custom Function domain. A GitHub
owner code search found no branch-ID references. Live App-backed
Session Search baseline was FOUND, four queried, zero unavailable.

A **single** attempted deletion of br-divine-glitter-b5tl65gc failed
with Neon HTTP 422: child branch br-old-union-b5qyign2 exists.
Independent active-branch readback showed no removal. That child is
parent of **the actual serving production branch**. Therefore #116
and its parent xai-candidate and descendant #147 candidate remain
untouched: removing them requires a separate supported branch-tree
migration/recreation with release and rollback validation, not
a routine housekeeping deletion. This is an observed structural
blocker, not conjecture about undiscovered consumers.

The independent October canary leaf
br-restless-hall-b5kaph3r had no children, was non-default and
unprotected. Its targeted deletion succeeded. Independent Neon active
list_branches omitted that ID, while include_deleted reported its
archived historical record. Production branch, original main and #166
pinned canary remained; production ssrouter2 deployment #8 was
re-read as completed. A **fresh App-backed search after deletion**
returned FOUND with the same four queried shards, zero unavailable,
and the two expected ChatGPT blue generation prefixes. No change
to production router code/config, source releases or catalog.

**Disposition:** 1 router canary leaf REMOVED, 3 old ancestors
BLOCKED_BY_CHILD_CHAIN and preserved, 0 production/rollback branches
mutated. The remaining historical envcanary116 Function names on
production-related branches are *inherited metadata* and have not
been separately removed. Do not delete them as part of this batch.
Avoid adding infrastructure just to flatten a branch tree.
No dollar savings are claimed.

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

**Evidence boundary:** Neon's Function-reader interface did not reveal the
literal current SESSION_SEARCH_CATALOG URL mapping; no other actual
consumer of the deleted projects was observed. The action was
bounded to the exact user-authorized two projects, backed by source/receipt
evidence, Function inventory, provider recoverability, and live
post-delete verification. Do not infer that deletion of another candidate
has passed these same gates. No cost saving has been calculated.

### Second batch — disposable issue137 and issue86 data branches, 2026-11-08

With the user's explicit instruction to continue the cleanup, exactly
five non-default, non-protected branches were removed:

- issue137: br-floral-boat-b49nvnr4 (spring-glade-75307688),
  br-wandering-snow-b5b0gb9f (plain-wave-74064803),
  br-spring-queen-b5paz3tw (morning-tooth-71193579),
  br-broad-wildflower-b5vcg9mu (wild-surf-61625738).
- issue86: br-shiny-forest-b58tar4p (wild-surf-61625738).

Each branch had zero Function deployments and zero custom domains, and
was neither default nor protected. GitHub code search found no references
to their exact branch IDs. The source work was already accepted:
issue137 strict/recall/auto serving was promoted and independently tested;
issue86 conflict-replay was accepted with an exact failed-mismatch
reproducer and immutable source/receipt. Read-only SQL proved all four
issue137 branches matched their parent main branch on exact generation
metadata and message/session counts (136/47,156; 246/55,161;
43/64,662; 60/17,790 respectively). The issue86 child intentionally
modified the generation's shard identity as a failure fixture while
retaining 60/17,790; its outcome is preserved in GitHub issue86.

Neon delete_branch returned success for each. Independent
list_branches WITHOUT include_deleted omitted each target and preserved
each default main. With include_deleted enabled, the old branches could
still appear with archived state; that is historical/recoverable metadata
rather than an active branch. A fresh real ChatGPT App search AFTER all
five deletions returned FOUND with four queried shards, zero unavailable,
and expected refreshed ChatGPT generation prefixes. No selected serving
project, default branch, Function, catalog or accepted source was
modified. Billed savings were not measured.

**Deliberately retained:** issue188 topk-eval branch still carries
Function deployments and belongs to an open research issue. The issue116
Lakebase BM25 A/B child has a private gold table and index, so it remains
until the test material has a verified durable recovery path. Neither is
misclassified as disposable based solely on its experiment label.

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
  shards, billed savings, and precise post-#166 retention gate. The completed first pair is
  verified from active inventory and healthy post-delete consumer search,
  not a claimed full URL/config disclosure. Resolve only these to approve the first
  deletion batch, rather than creating another benchmark.
