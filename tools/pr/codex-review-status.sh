#!/bin/sh
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'codex-review-status: not inside a Git worktree' >&2
    exit 2
}
cd "$ROOT"
PR=${1:-}
if [ -z "$PR" ]; then
    printf '%s\n' 'usage: tools/pr/codex-review-status.sh <pr-number>' >&2
    exit 2
fi
GH_BIN=${GH_BIN:-gh}
REPO=$($GH_BIN repo view --json nameWithOwner --jq .nameWithOwner)
HEAD=$($GH_BIN pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT HUP INT TERM
$GH_BIN api "/repos/$REPO/issues/$PR/comments?per_page=100" > "$TMP"
PYTHONPATH="$ROOT" python3 scripts/codex_review_status.py --head "$HEAD" --input "$TMP"
