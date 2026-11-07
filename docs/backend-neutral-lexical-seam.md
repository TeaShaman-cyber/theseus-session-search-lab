# Backend-neutral lexical seam

Coordination: issue #188.

This is a research architecture seam, not a production API or serving cutover.

## Plain mechanism

Session Search should own the meaning of lexical analysis. A database or search
vendor should not define what Russian morphology, mixed Russian/English technical
terms, or query selection mean.

raw document -> shared analyzer -> lexical tokens
raw historical query -> shared analyzer -> query planner -> lexical query
lexical query -> backend adapter -> Candidate v1[] -> rank / group / select

The backend adapter may be SQLite/FTS5, libSQL, Tantivy-derived search, Redis
Search, Postgres, or another implementation. Backend-specific query syntax does not
cross this boundary.

## Responsibilities

### Shared analyzer

Deterministically maps raw text to lexical tokens. Query and indexed documents must
use the same declared analyzer version for a comparison.

The analyzer may include Unicode normalization, language-aware stemming or
lemmatization, and protected technical-token handling. Hidden vendor tokenization
must not be treated as portable semantics.

### Query planner

Selects or weights analyzed query terms before backend execution. It is distinct
from morphology: #188 diagnostics show that better morphology alone is not enough,
while an oracle term-selection upper bound exposes large additional headroom.

A production planner must be target-independent. Gold targets may be used only for
offline evaluation.

### Backend adapter

Maps the portable lexical query into the backend native query/index mechanism and
projects returned rows into theseus.session-search-candidate.v1.

The adapter owns backend syntax, index lifecycle, and score orientation. It does
not own the linguistic meaning of the query.

### Candidate v1 and downstream logic

Candidate v1 remains the existing backend-neutral evidence boundary. Raw backend
scores are not assumed comparable across engines. Grouping, provenance and any
cross-backend fusion remain downstream concerns.

## Portability invariants

- accepted artifacts/ledger remain authority; lexical indexes are derived state;
- analyzer/query-planner version is explicit and reproducible;
- same document/query analyzer semantics can be exercised without SQL;
- backend failures remain explicit and do not become clean NO_SIGNAL;
- provider/shard/generation/session/message provenance survives adaptation;
- no backend-specific tokenizer, score or query syntax becomes canonical project
  semantics by accident.

## Promotion gate

Before any serving adoption:

1. freeze a new independent historical-query validation set;
2. freeze analyzer and target-independent query-planner semantics first;
3. compare at least the current backend and one alternative adapter through the
   same Candidate v1/conformance fixture;
4. measure candidate recall, Top-K/MRR, latency/size/attention cost and regressions;
5. preserve rollback and current serving until the new path is verified.

The #203 ten-case set is now development evidence for this new research line and
must not be reused as the final promotion holdout.
