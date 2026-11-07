# Independent holdout discovery (#203)

This experiment freezes discovery mechanics before evaluating the preselected local
candidate-policy challenger from #206.

## Boundary

The discovery script does **not** run current `(3,30)` or challenger `(7,30)` search.
It only scans the accepted local SQLite corpus for user/dialogue messages matching
the historical-retrieval attractors frozen in #203, ordered by create time.

Full candidate text, message IDs and session IDs are written only to a private
runtime file with mode 0600. The committed public receipt exposes only candidate
count, canonical digest and time bounds.

Manual eligibility review then proceeds in chronological order. A candidate is
accepted only if it asks to recall/find/reconstruct prior conversation/history and
has a defensible target in the accepted corpus. Current-web/external tasks, broad
multi-target requests, incidental uses of words such as 'вспомнил', and ambiguous
targets are rejected with reason. The first 10 valid independent targets are frozen.

Only after private holdout freeze + digest + current-policy baseline may `(7,30)` be
evaluated. The parameter grid from #201 must not be reopened on this holdout.

## Pre-query target lookup

`target_lookup.py` is a labeling helper, not an evaluator. It performs strict lexical
lookup only over messages with `create_time < holdout_query_time`. This prevents a
future assistant answer from becoming an accidental target and does not use current
recall policy, the cap=7 challenger, MMR, or any other candidate-policy experiment.

A target is accepted only after manual semantic verification that the earlier message
is what the historical query was trying to recover. No match or several plausible
matches means the candidate remains ambiguous/rejected rather than being forced into
the holdout.


## Accepted result

The first 10 eligible cases were frozen before challenger evaluation. Current
`(3,30)` reproduced the production-equivalent path on 10/10 cases and recovered the
target in 1/10 cases (R@10 = 0.10, MRR = 0.05).

The already-selected `(7,30)` challenger produced the **same target-rank vector**:
0 improvements, 10 unchanged, 0 regressions. Therefore cap=7 is rejected for lack
of independent holdout benefit and the current cap=3 remains unchanged.

This result does not identify the cause of the remaining 9/10 misses. It only shows
that relaxing the per-session cap from 3 to 7 is not the missing lever on this
holdout. Upstream candidate/query retrieval remains a separate causal branch under
#188. No parameter retuning on this holdout is permitted.
