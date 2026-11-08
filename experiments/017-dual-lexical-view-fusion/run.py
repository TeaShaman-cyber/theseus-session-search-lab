#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import pathlib
import sqlite3

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
H1_RUN_PATH = ROOT / "experiments" / "016-sparse-view-planner" / "run.py"
FUSION_PATH = HERE / "fusion.py"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


h1 = _load(H1_RUN_PATH, "h2_h1_run")
fusion = _load(FUSION_PATH, "h2_fusion")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_rank(baseline: int | None, challenger: int | None) -> str:
    b = math.inf if baseline is None else baseline
    c = math.inf if challenger is None else challenger
    if c < b:
        return "improved"
    if c > b:
        return "worse"
    return "same"


def evaluate(index_db: pathlib.Path, holdout: pathlib.Path) -> dict:
    import pymorphy3

    normalize_token = h1.analyzer.make_normalizer("lemma")
    morph = pymorphy3.MorphAnalyzer()
    cases = json.loads(holdout.read_text(encoding="utf-8"))

    conn = sqlite3.connect(f"file:{index_db.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    total_docs = int(conn.execute("SELECT count(*) FROM docs").fetchone()[0])

    rows = []
    try:
        for item in cases:
            features = h1._feature_rows(
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

            plan = h1.planner.plan_features(features, budget=5)

            full_candidates = h1.benchmark.candidate_rows(
                conn,
                table="fts",
                query_tokens=natural_tokens,
                per_session_cap=3,
                candidate_budget=30,
            )
            sparse_candidates = h1.benchmark.candidate_rows(
                conn,
                table="fts",
                query_tokens=plan["tokens"],
                per_session_cap=3,
                candidate_budget=30,
            )
            fused_candidates = fusion.fuse_rrf(
                full_candidates,
                sparse_candidates,
                k=60,
                limit=30,
            )

            baseline_rank = h1.benchmark.target_rank(
                full_candidates,
                item["target_content_fp"],
            )
            sparse_rank = h1.benchmark.target_rank(
                sparse_candidates,
                item["target_content_fp"],
            )
            fused_rank = h1.benchmark.target_rank(
                fused_candidates,
                item["target_content_fp"],
            )

            rows.append(
                {
                    "case_id": item["case_id"],
                    "baseline_rank": baseline_rank,
                    "h1_rank": sparse_rank,
                    "h2_rank": fused_rank,
                    "h2_vs_baseline": _compare_rank(baseline_rank, fused_rank),
                }
            )
    finally:
        conn.close()

    def summary(key: str) -> dict:
        vals = [row[key] for row in rows]
        return {
            "candidate_reachable": sum(value is not None for value in vals),
            "top10": sum(value is not None and value <= 10 for value in vals),
        }

    comparison = {
        key: sum(row["h2_vs_baseline"] == key for row in rows)
        for key in ("improved", "same", "worse")
    }

    return {
        "schema": "theseus.session-search-dual-lexical-rrf-h2.v1",
        "h2": {
            "name": "full_lemma_plus_frozen_h1_rrf",
            "rrf_k": 60,
            "candidate_budget_per_view": 30,
            "fused_candidate_budget": 30,
            "per_session_cap": 3,
            "fusion_sha256": _sha256(FUSION_PATH),
            "frozen_h1_planner_sha256": _sha256(
                ROOT / "experiments" / "016-sparse-view-planner" / "planner.py"
            ),
        },
        "holdout": {
            "cases": len(cases),
            "sha256": _sha256(holdout),
            "role": "h2_development_after_h1_independent_evaluation",
            "promotion_independent": False,
        },
        "index": {
            "documents": total_docs,
            "analyzer": "lemma_ru_pymorphy3_en_snowball",
        },
        "baseline": summary("baseline_rank"),
        "h1": summary("h1_rank"),
        "h2_result": summary("h2_rank"),
        "comparison_h2_vs_baseline": comparison,
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
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    args = parser.parse_args()

    payload = evaluate(args.index_db, args.holdout)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.receipt.chmod(0o600)

    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "baseline": payload["baseline"],
                "h1": payload["h1"],
                "h2": payload["h2_result"],
                "comparison_h2_vs_baseline": payload["comparison_h2_vs_baseline"],
                "privacy": payload["privacy"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
