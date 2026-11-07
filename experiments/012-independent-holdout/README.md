# Independent holdout discovery (#203)

This experiment freezes discovery mechanics before evaluating the preselected local
candidate-policy challenger from #201.

## Boundary

The discovery script does **not** run current `(3,30)` or challenger `(5,30)` search.
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

Only after private holdout freeze + digest + current-policy baseline may `(5,30)` be
evaluated. The parameter grid from #201 must not be reopened on this holdout.

## Pre-query target lookup

`target_lookup.py` is a labeling helper, not an evaluator. It performs strict lexical
lookup only over messages with `create_time < holdout_query_time`. This prevents a
future assistant answer from becoming an accidental target and does not use current
recall policy, the cap=5 challenger, MMR, or any other candidate-policy experiment.

A target is accepted only after manual semantic verification that the earlier message
is what the historical query was trying to recover. No match or several plausible
matches means the candidate remains ambiguous/rejected rather than being forced into
the holdout.
