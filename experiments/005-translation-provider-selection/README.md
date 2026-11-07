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
