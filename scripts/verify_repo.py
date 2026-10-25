#!/usr/bin/env python3
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED = [
    'README.md',
    'docs/qa.md',
    'docs/portable-runtime.md',
    'scripts/build_portable_runtime.py',
    'tests/test_portable_runtime.py',
    '.github/workflows/portable-runtime.yml',
    'tools/dev/check',
    'plugin-overlays/session-search-app-backed/README.md',
    'plugin-overlays/session-search-app-backed/skills/session-search-usage/SKILL.md',
    'plugin-overlays/session-search-app-backed/skills/session-search-usage/references/gateway.md',
    'scripts/verify_session_search_app_overlay.py',
    'receipts/064-app-backed-host-handoff.public.json',
    'receipts/064-federated-serving-final.public.json',
    'docs/projection-backend-sizing.md',
    'receipts/061-projection-backend-sizing.public.json',
    'docs/first-neon-promotion-plan.md',
    'docs/shard-bundle-transport.md',
    'docs/arcade-bundle-consumer.md',
    'receipts/075-first-neon-promotion-plan.public.json',
    'session_search/shard_allocation.py',
    'scripts/plan_neon_shards.py',
    'receipts/077-provider-aware-shard-allocation.public.json',
    'session_search/artifact_overlap.py',
    'scripts/audit_corpus_overlap.py',
    'receipts/081-artifact-overlap-guard.public.json',
    'receipts/083-xai-neon-materialization.public.json',
    'receipts/003-xai-drive-bundle.public.json',
    'receipts/003-arcade-bundle-consumer.public.json',
    'arcade/session-search-materializer/pyproject.toml',
    'arcade/session-search-materializer/README.md',
    'arcade/session-search-materializer/src/session_search_arcade_materializer/__init__.py',
    'arcade/session-search-materializer/src/session_search_arcade_materializer/bundle_consumer.py',
    'arcade/session-search-materializer/src/session_search_arcade_materializer/neon_loader.py',
    'arcade/session-search-materializer/src/session_search_arcade_materializer/server.py',
    '.github/workflows/deploy-session-search-arcade-materializer.yml',
    '.github/workflows/promote-session-search-ready.yml',
    'scripts/arcade_mark_bundle_ready.py',
    'tests/test_arcade_ready_ops.py',
    'session_search/shard_bundle.py',
    'scripts/shard_bundle.py',
    'session_search/neon_materializer.py',
    'scripts/materialize_neon_shard.py',
    'sql/neon/003_federated_generation_lifecycle.sql',
    'sql/neon/004_arcade_building_loader.sql',
    'tests/test_arcade_neon_loader.py',
    'scripts/codex_review_status.py',
    'tools/pr/codex-review-status.sh',
    'session_search/importer.py',
    'session_search/search.py',
    'session_search/chatgpt_export.py',
    'tests/test_chatgpt_export.py',
    'docs/architecture.md',
    'docs/capture-adapter-contract.md',
    'docs/research-lifecycle.md',
    'docs/wiki-bootstrap.md',
    'experiments/001-manual-bridge/README.md',
    'receipts/001-development-prototype.public.json',
    'receipts/001-wiki-bootstrap.json',
    'receipts/002-multipage-importer.public.json',
    'receipts/003-multi-session-corpus.public.json',
    'tests/test_bootstrap_contract.py',
    '.github/workflows/docs-check.yml',
]
for rel in REQUIRED:
    if not (ROOT / rel).is_file():
        raise SystemExit(f'VERIFY FAIL missing {rel}')

FORBIDDEN_PARALLEL_FEDERATION = [
    'session_search/federation.py',
    'tests/test_federated_search.py',
]
for rel in FORBIDDEN_PARALLEL_FEDERATION:
    if (ROOT / rel).exists():
        raise SystemExit(
            f'VERIFY FAIL unsupported parallel Python federation surface returned: {rel}'
        )

readme = (ROOT/'README.md').read_text()
for marker in [
    'MarcoPolo is not a runtime dependency',
    'Wiki: WIKI_GIT_REMOTE_VERIFIED',
    'fab484e1e22c982229d2aab2d80c933f4c5c1d93',
    'Session history is evidence, not semantic memory',
    'https://github.com/TeaShaman-cyber/theseus-research',
]:
    if marker not in readme:
        raise SystemExit(f'VERIFY FAIL README marker: {marker}')

workflow = (ROOT/'.github/workflows/docs-check.yml').read_text()
if 'run: ./tools/dev/check' not in workflow:
    raise SystemExit('VERIFY FAIL CI does not call canonical tools/dev/check')

portable_workflow = (ROOT/'.github/workflows/portable-runtime.yml').read_text()
if 'scripts/build_portable_runtime.py' not in portable_workflow:
    raise SystemExit('VERIFY FAIL portable workflow does not call canonical runtime builder')
consumer_marker = '  consume-runtime:'
if consumer_marker not in portable_workflow:
    raise SystemExit('VERIFY FAIL portable workflow missing independent consumer job')
consumer_section = portable_workflow.split(consumer_marker, 1)[1]
if 'actions/checkout@' in consumer_section:
    raise SystemExit('VERIFY FAIL portable consumer must not checkout repository')
if 'PORTABLE_RUNTIME_CONSUMER_ACCEPTANCE_PASS' not in consumer_section:
    raise SystemExit('VERIFY FAIL portable consumer acceptance postcondition missing')

json.loads((ROOT/'receipts/001-development-prototype.public.json').read_text())
json.loads((ROOT/'receipts/001-wiki-bootstrap.json').read_text())
json.loads((ROOT/'receipts/002-multipage-importer.public.json').read_text())
json.loads((ROOT/'receipts/003-multi-session-corpus.public.json').read_text())
json.loads((ROOT/'receipts/077-provider-aware-shard-allocation.public.json').read_text())
json.loads((ROOT/'receipts/081-artifact-overlap-guard.public.json').read_text())
json.loads((ROOT/'receipts/083-xai-neon-materialization.public.json').read_text())
json.loads((ROOT/'receipts/003-xai-drive-bundle.public.json').read_text())
json.loads((ROOT/'receipts/003-arcade-bundle-consumer.public.json').read_text())
tracked = subprocess.check_output(['git','ls-files'], cwd=ROOT, text=True).splitlines()
for p in tracked:
    lower=p.lower()
    if lower.endswith(('.zip','.sqlite','.sqlite3','.db')) or '/__pycache__/' in '/'+lower or lower.endswith('.pyc'):
        raise SystemExit(f'VERIFY FAIL forbidden tracked artifact: {p}')

# Reject patterns from the private development source or likely secret material.
prohibited = re.compile(
    r'barn-doctor-doctor-|/workspace/research/barn-session-search/raw|gh[opusr]_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9_-]{12,}',
    re.IGNORECASE,
)
for path in ROOT.rglob('*'):
    if not path.is_file() or '.git' in path.parts or path.suffix.lower() not in {'.md','.json','.py','.yml','.yaml'}:
        continue
    if path.name == 'verify_repo.py':
        continue
    text=path.read_text(errors='replace')
    if prohibited.search(text):
        raise SystemExit(f'VERIFY FAIL private/source marker in {path.relative_to(ROOT)}')

print(f'VERIFY PASS required={len(REQUIRED)} tracked={len(tracked)}')
