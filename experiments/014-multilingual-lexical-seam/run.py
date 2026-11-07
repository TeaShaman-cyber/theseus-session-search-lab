#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
ANALYZER_PATH = pathlib.Path(__file__).with_name("analyzer.py")
BENCHMARK_PATH = pathlib.Path(__file__).with_name("benchmark.py")


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


analyzer = _load(ANALYZER_PATH, "lexical_analyzer")
bench = _load(BENCHMARK_PATH, "lexical_benchmark")


def content_fp(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def build_index(source_db: pathlib.Path, out_db: pathlib.Path, *, mode: str) -> dict:
    normalize_token = analyzer.make_normalizer(mode)
    if out_db.exists():
        out_db.unlink()

    src = sqlite3.connect(f"file:{source_db.resolve()}?mode=ro&immutable=1", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(out_db)
    dst.row_factory = sqlite3.Row
    dst.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        CREATE TABLE docs(
          row_id INTEGER PRIMARY KEY,
          session_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          search_class TEXT NOT NULL,
          text_fp TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE fts USING fts5(tokens, content='');
        """
    )

    count = 0
    started = time.perf_counter()
    try:
        cur = src.execute(
            """
            SELECT row_id,session_id,ordinal,search_class,text
            FROM messages
            WHERE search_class IN ('dialogue','evidence')
            ORDER BY row_id
            """
        )
        docs_batch = []
        fts_batch = []
        for row in cur:
            tokens = analyzer.analyze_text(row["text"], normalize_token=normalize_token)
            docs_batch.append(
                (row["row_id"], row["session_id"], row["ordinal"], row["search_class"], content_fp(row["text"]))
            )
            fts_batch.append((row["row_id"], tokens))
            count += 1
            if len(docs_batch) >= 500:
                dst.executemany("INSERT INTO docs VALUES(?,?,?,?,?)", docs_batch)
                dst.executemany("INSERT INTO fts(rowid,tokens) VALUES(?,?)", fts_batch)
                dst.commit()
                docs_batch.clear()
                fts_batch.clear()
        if docs_batch:
            dst.executemany("INSERT INTO docs VALUES(?,?,?,?,?)", docs_batch)
            dst.executemany("INSERT INTO fts(rowid,tokens) VALUES(?,?)", fts_batch)
            dst.commit()
        quick = dst.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        src.close()
        dst.close()

    return {
        "mode": mode,
        "indexed": count,
        "quick_check": quick,
        "bytes": out_db.stat().st_size,
        "build_seconds": time.perf_counter() - started,
    }


def evaluate(index_db: pathlib.Path, holdout: pathlib.Path, *, mode: str) -> dict:
    normalize_token = analyzer.make_normalizer(mode)
    cases = json.loads(holdout.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{index_db.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    ranks = []
    try:
        for item in cases:
            query_tokens = [
                normalize_token(token)
                for token in analyzer.surface_tokens(item["query_text"])
            ]
            rows = bench.candidate_rows(
                conn,
                table="fts",
                query_tokens=query_tokens,
                per_session_cap=3,
                candidate_budget=30,
            )
            ranks.append(bench.target_rank(rows, item["target_content_fp"]))
    finally:
        conn.close()

    reachable = sum(rank is not None for rank in ranks)
    top10 = sum(rank is not None and rank <= 10 for rank in ranks)
    return {
        "mode": mode,
        "cases": len(cases),
        "candidate_reachable": reachable,
        "candidate_recall": reachable / len(cases),
        "top10_hits_bm25_order": top10,
        "r_at_10_bm25_order": top10 / len(cases),
        "ranks": ranks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--holdout", type=pathlib.Path, required=True)
    parser.add_argument("--mode", choices=["surface", "snowball", "lemma"], required=True)
    parser.add_argument("--index-db", type=pathlib.Path, required=True)
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    args = parser.parse_args()

    build = build_index(args.corpus, args.index_db, mode=args.mode)
    result = evaluate(args.index_db, args.holdout, mode=args.mode)
    payload = {
        "schema": "theseus.session-search-multilingual-lexical-arm.v1",
        "build": build,
        "result": result,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    args.receipt.chmod(0o600)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
