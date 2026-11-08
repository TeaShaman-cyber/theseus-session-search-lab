#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import pathlib
import re
import sqlite3

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ANALYZER_PATH = ROOT / "experiments" / "014-multilingual-lexical-seam" / "analyzer.py"
BENCHMARK_PATH = ROOT / "experiments" / "014-multilingual-lexical-seam" / "benchmark.py"
PLANNER_PATH = HERE / "planner.py"
_RU_RE = re.compile(r"^[а-яё]+$")


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


analyzer = _load(ANALYZER_PATH, "h1_analyzer")
benchmark = _load(BENCHMARK_PATH, "h1_benchmark")
planner = _load(PLANNER_PATH, "h1_planner")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _match_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _document_frequency(conn: sqlite3.Connection, token: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM fts WHERE fts MATCH ?",
        [_match_token(token)],
    ).fetchone()
    return int(row[0])


def _feature_rows(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    normalize_token,
    morph,
    total_docs: int,
) -> list[dict]:
    out = []
    for ordinal, surface in enumerate(analyzer.surface_tokens(query_text)):
        normalized = normalize_token(surface)
        if not normalized:
            continue
        pos = None
        grammemes: set[str] = set()
        if _RU_RE.fullmatch(surface):
            parsed = morph.parse(surface)[0]
            pos = parsed.tag.POS
            grammemes = set(parsed.tag.grammemes)
        df = _document_frequency(conn, normalized)
        idf = math.log((total_docs + 1) / (df + 1)) + 1.0
        out.append({
            "token": normalized,
            "ordinal": ordinal,
            "idf": idf,
            "kind": planner.classify_token(
                surface,
                normalized,
                pos=pos,
                grammemes=grammemes,
            ),
        })
    return out


def _compare_rank(baseline: int | None, planned: int | None) -> str:
    b = math.inf if baseline is None else baseline
    p = math.inf if planned is None else planned
    if p < b:
        return "improved"
    if p > b:
        return "worse"
    return "same"


def evaluate(index_db: pathlib.Path, holdout: pathlib.Path, *, budget: int) -> dict:
    import pymorphy3

    normalize_token = analyzer.make_normalizer("lemma")
    morph = pymorphy3.MorphAnalyzer()
    cases = json.loads(holdout.read_text(encoding="utf-8"))

    conn = sqlite3.connect(f"file:{index_db.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    total_docs = int(conn.execute("SELECT count(*) FROM docs").fetchone()[0])

    rows = []
    try:
        for item in cases:
            features = _feature_rows(
                conn,
                item["query_text"],
                normalize_token=normalize_token,
                morph=morph,
                total_docs=total_docs,
            )
            natural_tokens = []
            seen = set()
            for feature in features:
                token = feature["token"]
                if token not in seen:
                    seen.add(token)
                    natural_tokens.append(token)

            plan = planner.plan_features(features, budget=budget)
            baseline_candidates = benchmark.candidate_rows(
                conn,
                table="fts",
                query_tokens=natural_tokens,
                per_session_cap=3,
                candidate_budget=30,
            )
            planned_candidates = benchmark.candidate_rows(
                conn,
                table="fts",
                query_tokens=plan["tokens"],
                per_session_cap=3,
                candidate_budget=30,
            )
            baseline_rank = benchmark.target_rank(
                baseline_candidates,
                item["target_content_fp"],
            )
            planned_rank = benchmark.target_rank(
                planned_candidates,
                item["target_content_fp"],
            )
            rows.append({
                "case_id": item["case_id"],
                "baseline_rank": baseline_rank,
                "planner_rank": planned_rank,
                "comparison": _compare_rank(baseline_rank, planned_rank),
                "input_unique_tokens": plan["input_unique_tokens"],
                "selected_tokens": plan["selected_tokens"],
                "fallback": plan["fallback"],
            })
    finally:
        conn.close()

    baseline_reachable = sum(row["baseline_rank"] is not None for row in rows)
    planner_reachable = sum(row["planner_rank"] is not None for row in rows)
    baseline_top10 = sum(
        row["baseline_rank"] is not None and row["baseline_rank"] <= 10
        for row in rows
    )
    planner_top10 = sum(
        row["planner_rank"] is not None and row["planner_rank"] <= 10
        for row in rows
    )
    comparisons = {
        key: sum(row["comparison"] == key for row in rows)
        for key in ("improved", "same", "worse")
    }

    return {
        "schema": "theseus.session-search-sparse-view-planner-h1.v1",
        "planner": {
            "name": "sparse_view_key_concept_h1",
            "budget": budget,
            "target_independent": True,
            "planner_sha256": _sha256(PLANNER_PATH),
        },
        "holdout": {
            "cases": len(cases),
            "sha256": _sha256(holdout),
            "role": "development_diagnostic_after_203",
            "promotion_independent": False,
        },
        "index": {
            "documents": total_docs,
            "analyzer": "lemma_ru_pymorphy3_en_snowball",
            "per_session_cap": 3,
            "candidate_budget": 30,
        },
        "baseline": {
            "candidate_reachable": baseline_reachable,
            "top10": baseline_top10,
        },
        "h1": {
            "candidate_reachable": planner_reachable,
            "top10": planner_top10,
            "fallback_cases": sum(row["fallback"] for row in rows),
            "mean_selected_tokens": (
                sum(row["selected_tokens"] for row in rows) / len(rows)
                if rows else 0.0
            ),
        },
        "comparison": comparisons,
        "cases": rows,
        "privacy": {
            "query_text_exported": False,
            "selected_query_tokens_exported": False,
            "target_fingerprints_exported": False,
            "target_text_exported": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-db", type=pathlib.Path, required=True)
    parser.add_argument("--holdout", type=pathlib.Path, required=True)
    parser.add_argument("--budget", type=int, default=5)
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    args = parser.parse_args()

    payload = evaluate(args.index_db, args.holdout, budget=args.budget)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.receipt.chmod(0o600)
    print(json.dumps({
        "schema": payload["schema"],
        "baseline": payload["baseline"],
        "h1": payload["h1"],
        "comparison": payload["comparison"],
        "privacy": payload["privacy"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
