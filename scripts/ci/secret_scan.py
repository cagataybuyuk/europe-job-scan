from __future__ import annotations
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {'.git', '.pytest_cache', '__pycache__'}
TEXT_SUFFIXES = {'.py', '.gs', '.json', '.md', '.yml', '.yaml', '.toml', '.sh', '.html', '.txt'}
PATTERNS = {
    'private_key': re.compile(r'BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY'),
    'google_sa_private_key': re.compile(r'"private_key"\s*:'),
    'clasprc_refresh_token': re.compile(r'"refresh_token"\s*:'),
    'oauth_client_secret': re.compile(r'"client_secret"\s*:\s*"(?!\$\{|REDACTED|example)', re.I),
}
ALLOW = {
    'scripts/ci/secret_scan.py',
    'tests/unit/test_google_native_gd002.py',  # negative-test literals only
}
issues=[]
for p in ROOT.rglob('*'):
    if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
        continue
    rel=p.relative_to(ROOT).as_posix()
    if rel in ALLOW or p.suffix.lower() not in TEXT_SUFFIXES:
        continue
    try: text=p.read_text('utf-8')
    except UnicodeDecodeError: continue
    for name, rx in PATTERNS.items():
        if rx.search(text):
            issues.append(f'{rel}:{name}')
for apps_root in ['google_native/apps_script', 'google_native/apps_script_zero_cost']:
    for forbidden in ['.clasprc.json', '.clasp.json']:
        if (ROOT / apps_root / forbidden).exists():
            issues.append(f'{apps_root}/{forbidden}:forbidden_auth_or_runtime_mapping_file')
if issues:
    print('\n'.join(issues))
    sys.exit(1)
print('secret-scan: PASS')
