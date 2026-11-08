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
SELECTOR_PATH = HERE / "selector.py"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


h1 = _load(H1_RUN_PATH, "h3_h1_run")
selector = _load(SELECTOR_PATH, "h3_selector")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_rank(baseline: int | None, challenger: int | None) -> str:
    base = math.inf if baseline is None else baseline
    test = math.inf if challenger is None else challenger
    if test < base:
        return "improved"
    if test > base:
        return "worse"
    return "same"


def evaluate(index_db: pathlib.Path, holdout: pathlib.Path) -> dict:
    import pymorphy3

    normalize_token = h1.analyzer.make_normalizer("lemma")
    morph = pymorphy3.MorphAnalyzer()
    cases = json.loads(holdout.read_text(encoding="utf-8"))

    conn = sqlite3.connect(
        f"file:{index_db.resolve()}?mode=ro&immutable=1",
        uri=True,
    )
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
            decision = selector.select_view(features)

            natural_tokens = []
            seen = set()
            for feature in features:
                token = feature["token"]
                if token not in seen:
                    seen.add(token)
                    natural_tokens.append(token)

            sparse_plan = h1.planner.plan_features(features, budget=5)
            selected_tokens = (
                sparse_plan["tokens"]
                if decision["mode"] == "sparse"
                else natural_tokens
            )

            baseline_candidates = h1.benchmark.candidate_rows(
                conn,
                table="fts",
                query_tokens=natural_tokens,
                per_session_cap=3,
                candidate_budget=30,
            )
            selected_candidates = h1.benchmark.candidate_rows(
                conn,
                table="fts",
                query_tokens=selected_tokens,
                per_session_cap=3,
                candidate_budget=30,
            )

            baseline_rank = h1.benchmark.target_rank(
                baseline_candidates,
                item["target_content_fp"],
            )
            selected_rank = h1.benchmark.target_rank(
                selected_candidates,
                item["target_content_fp"],
            )
            rows.append(
                {
                    "case_id": item["case_id"],
                    "mode": decision["mode"],
                    "glue_ratio": decision["glue_ratio"],
                    "baseline_rank": baseline_rank,
                    "h3_rank": selected_rank,
                    "comparison": _compare_rank(
                        baseline_rank,
                        selected_rank,
                    ),
                }
            )
    finally:
        conn.close()

    def summary(key: str) -> dict:
        ranks = [row[key] for row in rows]
        return {
            "candidate_reachable": sum(rank is not None for rank in ranks),
            "top10": sum(
                rank is not None and rank <= 10
                for rank in ranks
            ),
        }

    return {
        "schema": "theseus.session-search-selective-sparse-h3.v1",
        "h3": {
            "name": "selective_sparse_glue_ratio_h3",
            "threshold": 0.5,
            "selector_sha256": _sha256(SELECTOR_PATH),
            "frozen_h1_planner_sha256": _sha256(
                ROOT
                / "experiments"
                / "016-sparse-view-planner"
                / "planner.py"
            ),
            "per_session_cap": 3,
            "candidate_budget": 30,
        },
        "holdout": {
            "cases": len(cases),
            "sha256": _sha256(holdout),
        },
        "baseline": summary("baseline_rank"),
        "h3_result": summary("h3_rank"),
        "routing": {
            "full": sum(row["mode"] == "full" for row in rows),
            "sparse": sum(row["mode"] == "sparse" for row in rows),
        },
        "comparison": {
            label: sum(row["comparison"] == label for row in rows)
            for label in ("improved", "same", "worse")
        },
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
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    args.receipt.chmod(0o600)

    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "baseline": payload["baseline"],
                "h3": payload["h3_result"],
                "routing": payload["routing"],
                "comparison": payload["comparison"],
                "privacy": payload["privacy"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
