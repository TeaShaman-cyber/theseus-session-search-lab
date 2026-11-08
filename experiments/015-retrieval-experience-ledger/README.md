# Experiment 015 — retrieval-experience ledger v0

Issue: #215.

This experiment proves the smallest durable feedback lifecycle before adding a
write-capable session_feedback serving tool.

Inputs:
- five hand-audited sanitized episodes from #182, #203, accepted history and
  PR #214 diagnostics;
- no raw query text;
- no target text;
- no serving mutation.

The processor validates JSONL and produces a deterministic pattern projection.
Policy promotion is deliberately impossible in v0.

Run:

    python3 scripts/process_retrieval_feedback.py       experiments/015-retrieval-experience-ledger/seeds.public.jsonl       /tmp/retrieval-patterns.json

Acceptance requires the seed set to include positive, regression and unknown
outcomes, and requires ineligible diagnostic episodes to remain visible without
contributing to eligible outcome counts.

## Live feedback refresh

The repository also contains a private-store refresh path:

    python3 scripts/refresh_retrieval_feedback.py \
      --episodes-output /private/retrieval-experience.jsonl \
      --patterns-output /private/retrieval-patterns.json

It reads the optional Upstash feedback store from environment configuration,
strictly validates feedback records, converts them into the same episode contract,
combines them with this seed ledger, and produces the same pattern projection.
No live store or schedule is created by this repository experiment.
