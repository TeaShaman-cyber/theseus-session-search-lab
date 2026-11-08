# Experiment 016 — sparse-view key-concept planner H1

Coordination: issue #188.

## Question

Can a small target-independent lexical planner recover part of the headroom seen
in experiment 014 without changing the backend, candidate budget, per-session cap,
or document representation?

The existing #203 ten-case set is development evidence only. It must not be used
as the final promotion holdout for this hypothesis.

## Frozen H1 rule

This rule is fixed before evaluating target ranks.

Input:

    analyzed natural-language query tokens + corpus document frequency

The planner classifies each token into one of these semantic classes:

1. identifier — numeric or digit-bearing token;
2. technical — non-stopword Latin technical token;
3. nominal — Russian noun or named-entity-like token;
4. qualifier — Russian adjective/participle/number-like qualifier;
5. other — remaining lexical content;
6. glue — closed-class words and explicit retrieval-instruction framing.

Explicit retrieval glue includes forms equivalent to:

    find/search/message/conversation/discuss/talk/write/mention/remember/chat/thread

in English and the corresponding fixed Russian lemma set.

Selection:

- deduplicate by normalized token;
- ignore glue while any content token exists;
- rank by semantic class first, corpus IDF second, original ordinal third;
- keep at most 5 anchors;
- return chosen anchors in original query order;
- if every token is glue, fall back explicitly to the original unique analyzed
  tokens rather than inventing an empty or synthetic query.

No target text, target fingerprint, gold rank, target session, or case-specific
rule is available to the planner.

## Development comparison

On the existing #203 development set compare H1 against:

- whole-query lemma baseline;
- experiment 014 rarest-term diagnostic;
- experiment 014 lemma oracle upper bound.

Do not tune H1 after seeing individual target ranks. A materially promising H1
must be frozen and evaluated on a new independent historical-query set before any
serving/backend decision.

No serving, storage, backend, index authority, or deployment changes are in scope.

## Development result

Frozen H1 was evaluated once on the existing #203 development set with the same
lemma analyzer, per-session cap 3 and global candidate budget 30.

| Arm | reachable / 10 | Top-10 / 10 |
| --- | ---: | ---: |
| whole-query lemma baseline | 2 | 2 |
| prior rarest-term diagnostic | max 3 | not selected |
| frozen H1 sparse-view planner | 4 | 2 |
| target-dependent lemma oracle | 8 | 7 |

H1 produced 3 improved / 7 same / 0 worse cases relative to the natural lemma
baseline. Two previously unreachable targets entered the Top-30 at ranks 26 and
12; one existing target improved from rank 9 to 7. No Top-10 count gain occurred.

This is a development signal, not a promotion result. H1 is now frozen unchanged.
The next gate is a new independent historical-query holdout. Do not tune the
planner on the ten #203 cases and do not select a backend or serving cutover from
this receipt.

Public aggregate evidence is in
receipts/188-sparse-view-planner-h1.public.json.
