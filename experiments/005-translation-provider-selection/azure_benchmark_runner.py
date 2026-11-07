#!/usr/bin/env python3
"""Run the frozen #127 Azure Translator benchmark without exposing private rows.

Runtime contract:
- database credentials arrive only through ISSUE127_DATABASE_URL;
- Azure credentials stay behind the existing az-owner wrapper;
- private query/translation text stays in process memory or the isolated DB;
- stdout contains aggregate evidence only.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse


CORE_PATH = Path(__file__).with_name("benchmark_core.py")
_CORE_SPEC = importlib.util.spec_from_file_location("issue127_benchmark_core", CORE_PATH)
_CORE = importlib.util.module_from_spec(_CORE_SPEC)
assert _CORE_SPEC.loader is not None
_CORE_SPEC.loader.exec_module(_CORE)

gold_digest = _CORE.gold_digest
translation_digest = _CORE.translation_digest
retrieval_metrics = _CORE.retrieval_metrics
latency_summary = _CORE.latency_summary

EXPECTED_GOLD_DIGEST_V2 = (
    "5c1e817960a3e8222ec9cdba6f9ec135b5c00d6e7be0f42a2dbf50775956a6d7"
)
DEFAULT_TRANSLATOR_ENDPOINT = (
    "https://theseus-translator.cognitiveservices.azure.com/"
    "translator/text/v3.0/translate?api-version=3.0&from=en&to=ru"
)
DEFAULT_AZ_OWNER = "/home/exedev/.local/bin/az-owner"
EVAL_SCHEMA = "issue127_eval"


def postgres_runtime(database_url: str) -> SimpleNamespace:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL_SCHEME_INVALID")
    if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL_INCOMPLETE")

    query = parse_qs(parsed.query)
    env = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": parsed.path.lstrip("/"),
        "PGUSER": unquote(parsed.username),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGSSLMODE": query.get("sslmode", ["require"])[0],
    }
    return SimpleNamespace(
        argv_prefix=["psql", "-X", "-v", "ON_ERROR_STOP=1", "-At"],
        env=env,
    )


def postgres_runtime_from_neon_api(
    *,
    api_base: str,
    project_id: str,
    branch_id: str,
    role_name: str,
    opener=urllib.request.urlopen,
) -> SimpleNamespace:
    base = api_base.rstrip("/")

    endpoints_request = urllib.request.Request(
        f"{base}/projects/{project_id}/endpoints",
        headers={"Accept": "application/json"},
    )
    with opener(endpoints_request, timeout=20) as response:
        endpoints = json.loads(response.read().decode("utf-8"))["endpoints"]

    matches = [
        endpoint
        for endpoint in endpoints
        if endpoint.get("branch_id") == branch_id
        and endpoint.get("type") == "read_write"
        and endpoint.get("host")
    ]
    if len(matches) != 1:
        raise RuntimeError(f"NEON_ENDPOINT_MATCH_COUNT={len(matches)}")

    password_request = urllib.request.Request(
        (
            f"{base}/projects/{project_id}/branches/{branch_id}/roles/"
            f"{role_name}/reveal_password"
        ),
        headers={"Accept": "application/json"},
    )
    with opener(password_request, timeout=20) as response:
        password = json.loads(response.read().decode("utf-8")).get("password")

    if not password:
        raise RuntimeError("NEON_ROLE_PASSWORD_EMPTY")

    return SimpleNamespace(
        argv_prefix=["psql", "-X", "-v", "ON_ERROR_STOP=1", "-At"],
        env={
            "PGHOST": matches[0]["host"],
            "PGPORT": "5432",
            "PGDATABASE": "neondb",
            "PGUSER": role_name,
            "PGPASSWORD": password,
            "PGSSLMODE": "require",
        },
    )


def historical_translation_digest(rows) -> str:
    payload = "\n".join(
        "\t".join(
            (
                row["case_id"],
                row["translated_ru"],
                row["target_content_fp"],
            )
        )
        for row in sorted(rows, key=lambda row: row["case_id"])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_frozen_gold(rows, *, expected_digest: str = EXPECTED_GOLD_DIGEST_V2):
    cases = {row["case_id"] for row in rows}
    en = sum(row["query_lang"] == "en" for row in rows)
    ru = sum(row["query_lang"] == "ru" for row in rows)
    digest = gold_digest(rows)

    if len(rows) != 20 or len(cases) != 10 or en != 10 or ru != 10:
        raise ValueError(
            f"GOLD_SHAPE_MISMATCH rows={len(rows)} cases={len(cases)} en={en} ru={ru}"
        )
    if digest != expected_digest:
        raise ValueError(
            f"GOLD_DIGEST_MISMATCH expected={expected_digest} actual={digest}"
        )
    return {
        "rows": len(rows),
        "cases": len(cases),
        "en": en,
        "ru": ru,
        "gold_digest_v2": digest,
    }


def public_receipt(
    *,
    provider_rows,
    ranks,
    gold_digest_v2: str,
    translation_digest_v2: str,
    historical_translation_digest: str,
):
    ordered = sorted(provider_rows, key=lambda row: row["case_id"])
    rank_values = [ranks[row["case_id"]] for row in ordered]
    metrics = retrieval_metrics(rank_values)
    latency = latency_summary([row["latency_ms"] for row in ordered])
    source_chars = sum(len(row["query_text"]) for row in ordered)

    return {
        "gold_digest_v2": gold_digest_v2,
        "translation_digest_v2": translation_digest_v2,
        "translation_digest_historical_tsv": historical_translation_digest,
        "provider_calls": len(ordered),
        "provider_success": sum("translated_ru" in row for row in ordered),
        "source_chars": source_chars,
        "latency": latency,
        **metrics,
    }


def _run_psql(runtime, *, sql: str | None = None, script: str | None = None) -> str:
    if (sql is None) == (script is None):
        raise ValueError("exactly one of sql or script is required")

    env = os.environ.copy()
    env.update(runtime.env)
    argv = list(runtime.argv_prefix)
    stdin = None
    if sql is not None:
        argv += ["-c", sql]
    else:
        argv += ["-f", "-"]
        stdin = script

    proc = subprocess.run(
        argv,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=180,
    )
    if proc.returncode:
        first = proc.stderr.splitlines()[0] if proc.stderr else "PSQL_FAILED"
        raise RuntimeError(first)
    return proc.stdout.strip()


def _json_query(runtime, sql: str):
    raw = _run_psql(runtime, sql=sql)
    return json.loads(raw or "[]")


def _schema_absent(runtime) -> bool:
    count = _run_psql(
        runtime,
        sql=(
            "select count(*) from information_schema.schemata "
            f"where schema_name='{EVAL_SCHEMA}'"
        ),
    )
    return int(count) == 0


def _fetch_gold(runtime):
    return _json_query(
        runtime,
        """
select coalesce(
  json_agg(
    json_build_object(
      'case_id',case_id,
      'query_lang',query_lang,
      'query_text',query_text,
      'target_content_fp',target_content_fp
    )
    order by case_id,query_lang
  ),
  '[]'::json
)::text
from issue116_eval.gold_queries;
""".strip(),
    )


def _get_access_token(az_owner: str) -> str:
    proc = subprocess.run(
        [
            az_owner,
            "account",
            "get-access-token",
            "--resource",
            "https://cognitiveservices.azure.com/",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode:
        raise RuntimeError("AZURE_TOKEN_ACQUISITION_FAILED")
    token = proc.stdout.strip()
    if not token:
        raise RuntimeError("AZURE_TOKEN_EMPTY")
    return token


def _translate_rows(en_rows, *, endpoint: str, token: str):
    translated = []
    for row in sorted(en_rows, key=lambda item: item["case_id"]):
        body = json.dumps(
            [{"Text": row["query_text"]}],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                latency_ms = (time.perf_counter() - started) * 1000.0
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"TRANSLATOR_HTTP_{exc.code}") from None

        translated.append(
            {
                "case_id": row["case_id"],
                "query_text": row["query_text"],
                "translated_ru": payload[0]["translations"][0]["text"],
                "target_content_fp": row["target_content_fp"],
                "latency_ms": latency_ms,
            }
        )
    return translated


def translation_freeze_script(path: Path) -> str:
    return f"""begin;
create schema {EVAL_SCHEMA};
create table {EVAL_SCHEMA}.translations (
  case_id text primary key,
  translated_ru text not null,
  target_content_fp text not null,
  latency_ms double precision not null,
  source_chars integer not null
);
\\copy {EVAL_SCHEMA}.translations (case_id,translated_ru,target_content_fp,latency_ms,source_chars) from '{path}' with (format csv, header true);
commit;"""


def _freeze_translations(runtime, rows):
    runtime_dir = Path(f"/run/user/{os.getuid()}")
    temp_dir = runtime_dir if runtime_dir.is_dir() else Path(tempfile.gettempdir())
    fd, path_text = tempfile.mkstemp(
        prefix="issue127-translations-", suffix=".csv", dir=temp_dir
    )
    path = Path(path_text)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "case_id",
                    "translated_ru",
                    "target_content_fp",
                    "latency_ms",
                    "source_chars",
                ]
            )
            for row in sorted(rows, key=lambda item: item["case_id"]):
                writer.writerow(
                    [
                        row["case_id"],
                        row["translated_ru"],
                        row["target_content_fp"],
                        f'{row["latency_ms"]:.9f}',
                        len(row["query_text"]),
                    ]
                )
        os.chmod(path, 0o600)

        _run_psql(runtime, script=translation_freeze_script(path))
    finally:
        path.unlink(missing_ok=True)


def _readback_translation_rows(runtime):
    return _json_query(
        runtime,
        f"""
select coalesce(
  json_agg(
    json_build_object(
      'case_id',case_id,
      'translated_ru',translated_ru,
      'target_content_fp',target_content_fp
    )
    order by case_id
  ),
  '[]'::json
)::text
from {EVAL_SCHEMA}.translations;
""".strip(),
    )


def _evaluate(runtime):
    script = f"""
begin;

create table {EVAL_SCHEMA}.terms as
with generated as (
  select t.case_id,
         t.target_content_fp,
         unnest(tsvector_to_array(to_tsvector('simple',t.translated_ru))) as lexeme
  from {EVAL_SCHEMA}.translations t
)
select g.case_id,
       g.target_content_fp,
       g.lexeme,
       (
         select count(*)::int
         from session_search.messages m
         where m.search_vector @@ (format('%L',g.lexeme)::tsquery)
       ) as doc_freq
from generated g;

create table {EVAL_SCHEMA}.queries as
with agg as (
  select case_id,
         target_content_fp,
         count(*) filter (where doc_freq between 1 and 128)::int as selected_terms,
         count(*) filter (where doc_freq > 0)::int as positive_terms,
         string_agg(format('%L',lexeme),' | ' order by doc_freq,lexeme)
           filter (where doc_freq between 1 and 128) as fallback_text,
         array_agg(lexeme order by lexeme)
           filter (where doc_freq > 0) as positive_lexemes
  from {EVAL_SCHEMA}.terms
  group by case_id,target_content_fp
)
select case_id,
       target_content_fp,
       fallback_text::tsquery as fallback_q,
       array_to_tsvector(positive_lexemes) as full_qvec,
       selected_terms,
       positive_terms
from agg;

create table {EVAL_SCHEMA}.results as
with ranked as (
  select q.case_id,
         q.target_content_fp,
         q.selected_terms,
         q.positive_terms,
         count(*) over (partition by q.case_id)::int as candidate_count,
         row_number() over (
           partition by q.case_id
           order by m.search_vector <@> to_bm25query(
                      q.full_qvec,
                      'session_search.messages_search_vector_bm25_issue116'::regclass
                    ) asc,
                    m.session_id,m.ordinal,m.message_id
         )::int as rn,
         md5(lower(trim(regexp_replace(m.text,'\\s+',' ','g')))) as content_fp
  from {EVAL_SCHEMA}.queries q
  join session_search.messages m
    on m.search_vector @@ q.fallback_q
)
select case_id,
       max(selected_terms)::int as selected_terms,
       max(positive_terms)::int as positive_terms,
       max(candidate_count)::int as candidate_count,
       min(rn) filter (where content_fp=target_content_fp)::int as target_rank
from ranked
group by case_id
order by case_id;

commit;
""".strip()
    _run_psql(runtime, script=script)

    return _json_query(
        runtime,
        f"""
select coalesce(
  json_agg(
    json_build_object(
      'case_id',case_id,
      'selected_terms',selected_terms,
      'positive_terms',positive_terms,
      'candidate_count',candidate_count,
      'target_rank',target_rank
    )
    order by case_id
  ),
  '[]'::json
)::text
from {EVAL_SCHEMA}.results;
""".strip(),
    )


def main() -> int:
    database_url = os.environ.get("ISSUE127_DATABASE_URL")
    if database_url:
        runtime = postgres_runtime(database_url)
    else:
        api_base = os.environ.get("ISSUE127_NEON_API_BASE")
        if not api_base:
            raise SystemExit(
                "ISSUE127_DATABASE_URL_OR_NEON_API_BASE_REQUIRED"
            )
        runtime = postgres_runtime_from_neon_api(
            api_base=api_base,
            project_id=os.environ.get(
                "ISSUE127_NEON_PROJECT_ID", "wild-surf-61625738"
            ),
            branch_id=os.environ.get(
                "ISSUE127_NEON_BRANCH_ID", "br-sweet-dream-b5qzaztb"
            ),
            role_name=os.environ.get(
                "ISSUE127_NEON_ROLE", "session_search_owner"
            ),
        )
    if not _schema_absent(runtime):
        raise SystemExit("ISSUE127_EVAL_SCHEMA_ALREADY_EXISTS")

    gold_rows = _fetch_gold(runtime)
    gold_summary = validate_frozen_gold(gold_rows)
    en_rows = [row for row in gold_rows if row["query_lang"] == "en"]

    token = _get_access_token(os.environ.get("ISSUE127_AZ_OWNER", DEFAULT_AZ_OWNER))
    provider_rows = _translate_rows(
        en_rows,
        endpoint=os.environ.get(
            "ISSUE127_TRANSLATOR_ENDPOINT", DEFAULT_TRANSLATOR_ENDPOINT
        ),
        token=token,
    )

    digest_v2 = translation_digest(provider_rows)
    digest_historical = historical_translation_digest(provider_rows)

    _freeze_translations(runtime, provider_rows)
    readback_rows = _readback_translation_rows(runtime)
    if translation_digest(readback_rows) != digest_v2:
        raise RuntimeError("TRANSLATION_READBACK_DIGEST_MISMATCH")

    results = _evaluate(runtime)
    ranks = {row["case_id"]: row["target_rank"] for row in results}
    receipt = public_receipt(
        provider_rows=provider_rows,
        ranks=ranks,
        gold_digest_v2=gold_summary["gold_digest_v2"],
        translation_digest_v2=digest_v2,
        historical_translation_digest=digest_historical,
    )
    receipt["per_case"] = [
        {
            "case_id": row["case_id"],
            "target_rank": row["target_rank"],
            "candidate_count": row["candidate_count"],
            "selected_terms": row["selected_terms"],
            "positive_terms": row["positive_terms"],
        }
        for row in results
    ]

    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
