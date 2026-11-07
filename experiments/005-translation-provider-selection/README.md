# Experiment 005 — reproducible query-translation provider selection

Issue: #121

## Question

Which EN↔RU translation path is proportionate for Session Search query normalization before semantic retrieval?

Criteria: reproducibility/versionability, EN↔RU suitability, latency, cost, privacy/locality, operational simplicity, and secret surface.

## Existing evidence

Issues #119–#120 established strong normalization signal on the frozen xAI shard:

- original EN: R@10 0.500, MRR 0.286;
- paired-RU oracle: R@10 1.000, MRR 0.803;
- model-produced EN→RU: R@10 1.000, MRR 0.753;
- all five EN top-10 failures were recovered.

The provider only needs translation quality adequate for retrieval normalization.

## Provider comparison — 2026-10-31

| Candidate | Versionability / observability | Cost floor | Privacy / locality | Disposition |
|---|---|---|---|---|
| Google Cloud Translation Advanced NMT | v3 API; explicit built-in model resource general/nmt; response reports model. Hosted weights are not immutable. | First 500k chars/month free, then $20/M chars. | Google says text is used only to provide the API, held briefly in memory, not used to train Translation; Advanced supports EU multi-regional processing. | **Preferred production-shaped spike** |
| Azure Translator v3 | Explicit API version 3.0; standard model is not an immutable checkpoint. | F0: 2M chars/month free. | Microsoft documents text translation as no-trace/no persistence; European endpoint stays inside EU boundary. | **Primary fallback / second spike** |
| DeepL Translate API | model_type selects latency/quality intent, but DeepL may change the concrete model behind it; model_type_used is observable. | Developer plan: 1M chars total, no credit card. | Paid security terms are strong; free/developer privacy is less attractive for private project queries. | **Quality challenger, not reproducibility leader** |
| Meta NLLB-200 distilled 600M | Exact HF revision can be pinned. | Local compute only. | Fully local after fetch. | **Reject for production**: ~2.48 GB, CC-BY-NC-4.0, model card says research/not production. |

## Primary sources

Google:
- https://docs.cloud.google.com/translate/docs/advanced/nmt-model
- https://docs.cloud.google.com/translate/docs/reference/rest/v3/projects/translateText
- https://docs.cloud.google.com/translate/docs/advanced/endpoints
- https://cloud.google.com/products/translate/pricing
- https://docs.cloud.google.com/translate/data-usage
- https://docs.cloud.google.com/translate/docs/languages

Azure:
- https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/v3/reference
- https://learn.microsoft.com/en-us/azure/ai-services/translator/language-support
- https://azure.microsoft.com/en-us/pricing/details/translator/
- https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/translator/data-privacy-security
- https://learn.microsoft.com/en-us/privacy/eudb/eu-data-boundary-configure-azure-nonregional-services

DeepL:
- https://developers.deepl.com/docs/translate/understanding-model-types
- https://developers.deepl.com/docs/getting-started/supported-languages
- https://support.deepl.com/hc/en-us/articles/360021200939-DeepL-API-plans
- https://support.deepl.com/hc/en-us/articles/9773914250012-About-DeepL-API

Local NLLB:
- https://huggingface.co/facebook/nllb-200-distilled-600M

## Reproducibility contract

No standard hosted candidate here exposes immutable public translation weights.

Treat reproducibility as:

provider + API version + requested model/mode + endpoint/region + source/target language + exact input hash + exact output hash + timestamp + response model/mode metadata + retrieval acceptance result.

Vendor model updates are drift to detect, not something the client can eliminate.

## Recommendation

### First spike — Google Cloud Translation Advanced NMT

Use v3 translateText, source en, target ru, explicit general/nmt, EU endpoint, and the immutable 10-query set from #120. Capture request/output hashes and returned model metadata.

Why first:
- strongest observable hosted contract of the candidates;
- cheap enough for continuous canaries;
- documented privacy and EU processing;
- no LLM prompting or embedding stack.

### Fallback — Azure Translator v3

Run the same frozen benchmark. Prefer Azure instead if setup is materially simpler, latency is better, or its output meets the same retrieval acceptance threshold. Its 2M-char F0 allowance is attractive for recurring canaries.

### DeepL challenger

Only add after one production-shaped path passes. Use it to challenge quality/latency, not as the reproducibility reference, because concrete model selection is intentionally abstracted.

### Local NLLB

Do not promote. Pinning does not offset production/license and serving-cost problems for this workload.

## Next live-spike acceptance

A provider advances only if:

1. all 10 frozen EN queries translate;
2. target R@10 remains 1.000;
3. MRR is not materially worse than #120's 0.753;
4. model/mode metadata and input/output hashes are capturable;
5. p95 provider latency is measured;
6. only query strings are sent, never source corpus text;
7. credentials remain outside repository/model-visible output;
8. provider outage falls back to the existing untranslated lexical path.

Disposition:

GOOGLE_NMT_FIRST_SPIKE / AZURE_TRANSLATOR_FALLBACK / DEEPL_QUALITY_CHALLENGER / NLLB_NO_PRODUCTION / NO_CUTOVER


## Issue #127 — Azure benchmark reproducibility checkpoint (2026-11-07)

This is a continuation of the provider-selection line, not a rewrite of the historical #121 decision.

### Frozen authority

The benchmark still uses the isolated xAI child branch created for #116:

- project: `wild-surf-61625738`;
- branch: `br-sweet-dream-b5qzaztb`;
- source table: `issue116_eval.gold_queries`;
- 20 rows = 10 paired cases, 10 EN queries + 10 RU queries;
- private query strings and target message identifiers stay in the isolated benchmark branch.

The historical public frozen-set SHA-256 `0039d1d6d4996ef0d456590d0de9b97ae4c7dff880df378802386f68d3b07fb9`
remains valid historical evidence, but the exact serialization procedure used to compute it was not recoverable from Git or Session Search. Do not silently invent that method.

For #127, `benchmark_core.py` therefore defines a new explicit canonical digest over
`case_id + query_lang + query_text + target_content_fp`, sorted by
`(case_id, query_lang)`, serialized as compact UTF-8 JSON with sorted keys.

Current live readback:

- `gold_digest_v2 = 5c1e817960a3e8222ec9cdba6f9ec135b5c00d6e7be0f42a2dbf50775956a6d7`;
- rows = 20;
- cases = 10;
- EN = 10;
- RU = 10.

### Recovered #120 evaluator

The old reasoning-model translation arm was reproduced from the surviving database state before running Azure:

1. `to_tsvector('simple', translated_ru)` defines the unique query lexemes;
2. per-lexeme document frequency is the count of `session_search.messages` whose
   `search_vector` matches that lexeme;
3. candidate terms are positive-df terms with `df <= 128`;
4. `fallback_q` is the OR query ordered by `(doc_freq, lexeme)`;
5. BM25 ranking context is `array_to_tsvector(...)` over all positive-df lexemes;
6. candidates satisfy `m.search_vector @@ fallback_q`;
7. ranking is ascending
   `m.search_vector <@> to_bm25query(full_qvec, 'messages_search_vector_bm25_issue116')`
   with deterministic `session_id, ordinal, message_id` tie-breakers;
8. target content fingerprint is
   `md5(lower(trim(regexp_replace(text, '\\s+', ' ', 'g'))))`.

Independent live checks against the surviving #120 tables:

- generated lexemes: 121/121 exact;
- document frequencies: 121/121 exact;
- stored `fallback_q`: 10/10 exact;
- stored `full_qvec`: 10/10 exact;
- target fingerprints: 10/10 exact;
- recomputed target ranks: 10/10 exact;
- historical aggregate reproduced exactly:
  `R@1=0.600, R@3=0.900, R@5=1.000, R@10=1.000, MRR=0.753`.

This control is the acceptance oracle for the Azure arm. Only the translation output may change.

### Azure execution status

The exe.dev Azure operator identity is separate infrastructure owned by
`TeaShaman-cyber/theseus-cloud-shell`. #127 does not own or persist cloud-shell credentials.

On 2026-11-07 the operator service principal was independently verified against the
Translator data plane:

- exact-resource `Cognitive Services User` assignment;
- correct Cognitive Services token audience;
- custom-domain Bearer-only translation returned HTTP 200;
- three fresh-process/fresh-token canaries returned HTTP 200;
- the global endpoint also returned HTTP 200 when both exact Resource ID and
  `Ocp-Apim-Subscription-Region: northeurope` were supplied.

The earlier custom-domain `401 PermissionDenied` cleared without any further role,
credential, networking, or resource mutation. Treat the exact internal propagation/cache
mechanism as UNKNOWN; do not duplicate the role assignment or add static Translator keys.

### Versioned #127 runner

`azure_benchmark_runner.py` is the production-shaped benchmark executor. Its purpose is
to keep orchestration out of chat/tool payloads:

1. resolve PostgreSQL credentials only at runtime;
2. verify the exact frozen 20-row / 10-case authority and `gold_digest_v2`;
3. translate the 10 EN queries through the verified Azure custom endpoint;
4. compute the translation digest before inspecting retrieval results;
5. freeze translations into the isolated benchmark branch;
6. run the recovered #120 fallback + BM25 evaluator unchanged;
7. print only aggregate/per-case rank evidence — never query text, translation text,
   target fingerprints, database URLs, passwords, or bearer tokens.

Credential inputs:

- preferred generic path: `ISSUE127_DATABASE_URL`;
- exe.dev path: `ISSUE127_NEON_API_BASE` points at the provider-side Neon API proxy,
  which resolves the branch endpoint and role password inside the process;
- Azure authentication remains behind the existing `az-owner` wrapper.

Accepted exe.dev invocation shape:

```sh
ISSUE127_NEON_API_BASE=https://neon.int.exe.xyz/api/v2 ISSUE127_NEON_ROLE=neondb_owner python3 experiments/005-translation-provider-selection/azure_benchmark_runner.py
```

The benchmark branch is isolated; this runner does not modify serving, activation,
candidate catalogs, or production shard state.

### Issue #127 final Azure benchmark result — 2026-11-07

The versioned runner was executed from exact Git SHA
`bbb020ec43a33372bb01fb1d9d172a3098b3f80d` against the isolated frozen branch.

Provider execution:
- 10/10 Azure Translator calls: HTTP 200;
- source characters: 765;
- latency mean: 231.3 ms;
- latency p50: 220.8 ms;
- latency p95: 291.0 ms;
- historical TSV translation digest:
  `38dff1e437d31469874179e96799599a7af11d4ade2f18f0e630837de756de95`;
- canonical translation digest v2:
  `6c92cce535016adb8f642083d483348a89ebb04dc9e4720f98d9006901e1de58`.

The historical TSV digest exactly matches the earlier pre-retrieval Azure run on the same
frozen set, so the observed Azure output was reproducible across the two executions.

Retrieval result under the unchanged recovered #120 evaluator:
- R@1 = 0.300;
- R@3 = 0.500;
- R@5 = 0.800;
- R@10 = 0.900;
- MRR = 0.465.

Comparison with the prior reasoning-model translation arm
(`R@1=0.600, R@3=0.900, R@5=1.000, R@10=1.000, MRR=0.753`):

| Case | Prior rank | Azure rank |
|---|---:|---:|
| basic_memory_local | 2 | miss |
| deepseek_moe_lens | 5 | 5 |
| family_gramps_upload | 2 | 2 |
| github_mcp_optional | 1 | 5 |
| gramps_sqlite_postgres | 3 | 3 |
| hindsight_name | 1 | 6 |
| infisical_failure_point | 1 | 1 |
| notion_drive_connectors | 1 | 1 |
| remote_linux_vs_marcopolo | 1 | 1 |
| residual_oauth_access | 1 | 4 |

Six cases were unchanged; four degraded; none improved.

Decision:

`AZURE_TRANSLATOR_OPERATIONALLY_VERIFIED / TRANSLATION_OUTPUT_REPRODUCIBLE /
RETRIEVAL_QUALITY_GATE_FAILED / DO_NOT_ADVANCE_AZURE_STANDARD_NMT /
NO_SERVING_CUTOVER`.

Azure remains a technically viable authenticated provider, but standard Translator NMT does not
meet the frozen retrieval-quality acceptance threshold for Session Search query normalization.
The next provider experiment, if pursued, should use the same frozen authority and evaluator
rather than changing ranking or candidate generation.

### Issue #174 — OpenRouter free-model benchmark snapshot (2026-11-07)

The first live OpenRouter run used exact Git SHA
`ff9a414405b1b227e6f88efe9d8402fb11b439b9`.

The runner pins exact model + provider, disables provider fallback and model reasoning,
uses `temperature=0` with fixed seed `127`, attempts all provider arms before reading
any retrieval result, and keeps the frozen #120/#127 evaluator unchanged.

#### Nemotron 3 Ultra — complete result

Arm:
- model: `nvidia/nemotron-3-ultra-550b-a55b:free`;
- provider tag: `nvidia`;
- response provider: `Nvidia`;
- 10/10 provider calls completed;
- provider-reported total cost: `0`;
- source characters: 765;
- latency mean: 326.6 ms;
- p50: 330.9 ms;
- p95: 341.3 ms;
- historical TSV translation digest:
  `f67593ec68662ab54a699e73adbbd7354742f5eea38a2cc5c92a6e772424400a`;
- canonical translation digest v2:
  `74fa1e501dcebcd3e4fe2346a912e980c14698df92ceb79fb6dac5a6356fed8b`.

Retrieval:
- R@1 = 0.500;
- R@3 = 0.800;
- R@5 = 1.000;
- R@10 = 1.000;
- MRR = 0.690.

Reference:
- Azure Translator: R@10 0.900 / MRR 0.465;
- prior active-reasoning-model translation: R@10 1.000 / MRR 0.753;
- paired-RU oracle: R@10 1.000 / MRR 0.803.

Compared with the prior model-translation arm, Nemotron produced the same target rank
on 8/10 cases, improved one case from 3 -> 2, and regressed one case from 1 -> 5.

Independent Neon aggregate readback confirmed:
10 translation rows, 10 result rows, 5 rank-1 hits, 8 rank<=3 hits,
10 rank<=5 hits, 10 rank<=10 hits, MRR 0.690.

#### Xing4 / Gemma 4 — no quality verdict yet

The exact pinned provider calls returned HTTP 429 before a complete 10-query translation
set could be frozen:

- `xingchen-agi/xing4.0-29b-a4b:free` / `phala` -> 429;
- `google/gemma-4-31b-it:free` / `google-ai-studio` -> 429.

No `issue174_xing4` or `issue174_gemma4` schema exists and no retrieval result was
computed for either arm. Treat both as `PROVIDER_UNAVAILABLE_NOW`, not model-quality
failures.

Current disposition:

`NEMOTRON_FREE_R10_PASS / NEMOTRON_MRR_0_690 / COST_ZERO /
AZURE_OUTPERFORMED / PRIOR_MODEL_BASELINE_APPROACHED /
XING4_PROVIDER_429 / GEMMA4_PROVIDER_429 / NO_CUTOVER`.

### Issue #176 — BotHub paid-model benchmark (2026-11-07)

BotHub was exercised through the verified exe.dev `bothub-api` HTTP proxy using
the same frozen authority and unchanged evaluator as #120/#127/#174.

Frozen gold:
- rows: 20;
- cases: 10;
- `gold_digest_v2 = 5c1e817960a3e8222ec9cdba6f9ec135b5c00d6e7be0f42a2dbf50775956a6d7`.

Execution:
- first live run: exact SHA
  `cd646dcc3f889a24631004faa4b84d641b50bb2a`,
  UTC `2026-11-07T13:10:44Z..13:11:54Z`;
- BotHub canonicalized two response model IDs with provider prefixes, which the
  first strict guard classified as model drift even though the HTTP calls
  succeeded;
- the canonical-ID/resume fix was verified and committed;
- resumed run: exact SHA
  `2de28f8006683f3e28c19daef7fa16a86b320be4`,
  UTC `2026-11-07T13:13:40Z..13:14:40Z`.

| Arm | R@1 | R@3 | R@5 | R@10 | MRR | mean ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gemini 2.5 Flash Lite | 0.300 | 0.500 | 0.700 | 0.900 | 0.449 | 5599 | 8866 |
| GPT-4.1 Nano | 0.400 | 0.800 | 0.900 | 0.900 | 0.593 | 2341 | 5241 |
| DeepSeek V4.1 Flash | 0.300 | 0.800 | 1.000 | 1.000 | 0.557 | 2489 | 3382 |

Independent Neon aggregate readback matched all three rows above.

Token observations from the successful ten-query arms:

| Arm | prompt | completion | reasoning | total |
|---|---:|---:|---:|---:|
| Gemini 2.5 Flash Lite | 800 | 3639 | 3446 | 4439 |
| GPT-4.1 Nano | 909 | 209 | 0 | 1118 |
| DeepSeek V4.1 Flash | 1123 | 1836 | 1599 | 2959 |

Gemini and DeepSeek spent most of their completion budget on reasoning for this
simple translation task. GPT Nano was much more token-efficient.

The public BotHub tariff snapshot used by the runner was:
- Gemini 2.5 Flash Lite: 4.38 RUB/M input, 17.53 RUB/M output;
- GPT-4.1 Nano: 4.38 RUB/M input, 17.53 RUB/M output;
- DeepSeek V4.1 Flash: 4.25 RUB/M input, 47.21 RUB/M output.

Those values are **catalog estimates, not billing truth**. BotHub's provider mode
(ECO/PRO), actual provider route and internal CAPS accounting are separate
variables. Billing interpretation belongs to
`TeaShaman-cyber/theseus-model-usage-lab#17`.

Quality references:
- Azure Translator: R@10 0.900 / MRR 0.465;
- OpenRouter Nemotron snapshot: R@10 1.000 / MRR 0.690;
- prior active-reasoning-model translation: R@10 1.000 / MRR 0.753;
- paired-RU oracle: R@10 1.000 / MRR 0.803.

Decision:

`BOTHUB_PAID_MODELS_MEASURED / NO_CANDIDATE_PASSES_QUALITY_GATE /
DEEPSEEK_R10_PASS_MRR_FAIL / GPT_NANO_MRR_BEST_BUT_R10_FAIL /
GEMINI_FLASH_LITE_REJECTED / NO_SERVING_CUTOVER`.

The experiment does not justify a production translation-provider change.

### Issue #178 — Azure + local Phi repair benchmark (2026-11-07)

The hybrid arm was executed from exact Git SHA
`cf4af3c` with the frozen #120/#127 authority and unchanged retrieval evaluator.

Runtime:
- Azure Translator output reused as the draft stage;
- repair model: `microsoft/Phi-4-mini-instruct`;
- quant: `Q4_K_M` (~2.49 GB GGUF);
- runtime: LM Studio native Windows, CPU-only, context 1024, parallel 1;
- model API bound to localhost only;
- 10/10 repair calls completed;
- prompt tokens: 1107;
- completion tokens: 208;
- total tokens: 1315;
- mean repair latency: 10343.7 ms;
- p50: 9340.5 ms;
- p95: 14695.0 ms;
- canonical repaired-translation digest v2:
  `5cbc185c702db5b84bda05d667f40dbbdf3c2ef712402d987be20ac9ea64ffcb`.

Retrieval under the unchanged evaluator:
- R@1 = 0.500;
- R@3 = 0.500;
- R@5 = 0.700;
- R@10 = 0.800;
- MRR = 0.557.

Reference:
- Azure Translator: R@10 0.900 / MRR 0.465;
- OpenRouter Nemotron snapshot: R@10 1.000 / MRR 0.690;
- prior active-reasoning-model translation: R@10 1.000 / MRR 0.753;
- paired-RU oracle: R@10 1.000 / MRR 0.803.

Per-case change relative to Azure:
- `family_gramps_upload`: rank 2 -> 1;
- `gramps_sqlite_postgres`: rank 3 -> 1;
- `residual_oauth_access`: rank 4 -> miss;
- the other seven cases were unchanged.

Interpretation: local Phi repair is not a no-op — it materially improves two Azure
queries — but unconditional repair is not reliable enough for this frozen set because
it also destroys one previously recoverable top-10 result. The aggregate MRR improvement
does not compensate for the R@10 regression, and the hybrid remains below the Nemotron
reference.

Decision:

`HYBRID_AZURE_LOCAL_PHI_MEASURED / MRR_IMPROVED_OVER_AZURE /
R10_REGRESSED / NEMOTRON_NOT_REACHED / BLANKET_REPAIR_REJECTED /
NO_PAID_MODEL_PROVIDER_DEPENDENCY / NO_SERVING_CUTOVER / STOP`.

Do not expand this issue into a model zoo or tune the frozen evaluator. A future gated or
selective-repair design would be a separate hypothesis with its own acceptance criteria.

### Issue #180 — Azure + local Phi-3.5 repair, frozen-10 comparison (2026-11-07)

One candidate only: microsoft/Phi-3.5-mini-instruct GGUF Q4_K_M,
bartowski/Phi-3.5-mini-instruct-GGUF, 2,393,232,672 bytes,
SHA-256 e4165e3a71af97f1b4820da61079826d8752a2088e313af0c7d346796c38eff5.
Runtime: native Windows LM Studio localhost, CPU-only, parallel 1. Model imported
through a symbolic link; no second GGUF copy required. CLI requested
context 1024, but actual lms ps reported 66304. This is an observed configuration
mismatch. The llama-server process consumed ~18–21 GB working set at observation;
latency and memory are NOT controlled apples-to-apples versus #178.

Cold load: ~190 seconds (CLI reported 3m 5.51s). First API canary took 22.8s,
returned finish_reason=length at max_tokens=8; warm canary took 12.9s and
returned finish_reason=stop at max_tokens=48. Neither counted among frozen ten.

Frozen authority: same #120/#127 gold digest v2
5c1e817960a3e8222ec9cdba6f9ec135b5c00d6e7be0f42a2dbf50775956a6d7,
unchanged Azure draft, prompts, temperature=0, seed=178, max_tokens=192 and
unchanged retrieval evaluator, isolated schema issue180_azure_phi35.

- Calls: 10/10 success; 1457 prompt tokens, 321 completion tokens, total 1778.
- Warm repair mean 14,756.3 ms; p50 13,820.4 ms; p95 19,162.7 ms.
- Repaired translation digest v2:
  312eccc72d3751ed0d27a28acd89db29b49fb7a15353a1d85367c1853b9f400f.
- Retrieval: R@1 0.400, R@3 0.600, R@5 0.600, R@10 0.800,
  MRR 0.497619.
- Compared with Azure (R@10 0.900, MRR 0.465), Phi-4-mini (#178:
  0.800, 0.557), Nemotron (1.000, 0.690).

Per-case target ranks (Phi-4-mini -> Phi-3.5-mini):
- basic_memory_local: miss -> miss;
- deepseek_moe_lens: 5 -> 3;
- family_gramps_upload: 1 -> miss;
- github_mcp_optional: 5 -> 1;
- gramps_sqlite_postgres: 1 -> 6;
- hindsight_name: 6 -> 1;
- infisical_failure_point: 1 -> 1;
- notion_drive_connectors: 1 -> 3;
- remote_linux_vs_marcopolo: 1 -> 1;
- residual_oauth_access: miss -> 7.

Interpretation: Phi-3.5 and Phi-4 have materially different per-case behavior,
but Phi-3.5 neither restores R@10 relative to Azure nor improves MRR over Phi-4.
The older-model anecdote remains unverified. STOP: no replacement, serving
cutover or model zoo expansion. Any Microsoft Windows ML / optimized ONNX runtime
comparison is a separately authorized hypothesis, not conflated with this
model comparison.
