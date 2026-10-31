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
