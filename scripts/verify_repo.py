#!/usr/bin/env python3
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED = [
    'README.md',
    'session_search/importer.py',
    'session_search/search.py',
    'docs/architecture.md',
    'docs/capture-adapter-contract.md',
    'docs/research-lifecycle.md',
    'docs/wiki-bootstrap.md',
    'experiments/001-manual-bridge/README.md',
    'receipts/001-development-prototype.public.json',
    'receipts/001-wiki-bootstrap.json',
    'tests/test_bootstrap_contract.py',
    '.github/workflows/docs-check.yml',
]
for rel in REQUIRED:
    if not (ROOT / rel).is_file():
        raise SystemExit(f'VERIFY FAIL missing {rel}')

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

json.loads((ROOT/'receipts/001-development-prototype.public.json').read_text())
json.loads((ROOT/'receipts/001-wiki-bootstrap.json').read_text())
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
