#!/bin/sh
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'alternative-review-status: not inside a Git worktree' >&2
    exit 2
}
cd "$ROOT"

PR=${1:-}
EXPECTED_HEAD=${2:-}
if [ -z "$PR" ] || [ -z "$EXPECTED_HEAD" ]; then
    printf '%s\n' 'usage: tools/pr/alternative-review-status.sh <pr-number> <expected-head>' >&2
    exit 2
fi

REPO=${GITHUB_REPOSITORY:-}
if [ -z "$REPO" ]; then
    REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
fi

gh api --paginate --slurp "repos/$REPO/issues/$PR/comments" |
    PYTHONPATH="$ROOT" python3 "$ROOT/scripts/alternative_review_status.py" --head "$EXPECTED_HEAD"
