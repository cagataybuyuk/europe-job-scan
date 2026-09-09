from pathlib import Path
import sys

root=Path(__file__).resolve().parents[2]
contract=(root/'src/ejs/contracts/google_native.py').read_text()
gh_contract=(root/'src/ejs/contracts/github_executor.py').read_text()
apps='\n'.join(p.read_text() for p in (root/'google_native/apps_script').glob('*.gs'))
zero_cost_apps='\n'.join(p.read_text() for p in (root/'google_native/apps_script_zero_cost').glob('*.gs'))
required=[
    ('google-native contract forbids form write', 'form_write' in contract and 'PermissionError' in contract),
    ('google-native contract forbids file upload', 'file_upload' in contract and 'PermissionError' in contract),
    ('google-native contract forbids final submit', 'final_submit' in contract and 'PermissionError' in contract),
    ('Apps Script TEST target present', '17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM' in apps),
    ('Apps Script PROD deny target present', '1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg' in apps),
    ('GD004 contract is TEST-only', 'EJS-GH-EXEC-0.1' in gh_contract and 'must target TEST' in gh_contract),
    ('GD004 contract forbids mutating capability', 'GD-004 mutating capabilities are forbidden' in gh_contract),
    ('GD004 Apps Script TEST target present', '17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM' in zero_cost_apps),
    ('GD004 Apps Script PROD deny target present', '1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg' in zero_cost_apps),
    ('GD004 Apps Script forbids form write', 'GD004_FORM_WRITE_FORBIDDEN' in zero_cost_apps),
    ('GD004 Apps Script forbids file upload', 'GD004_FILE_UPLOAD_FORBIDDEN' in zero_cost_apps),
    ('GD004 Apps Script forbids final submit', 'GD004_FINAL_SUBMIT_FORBIDDEN' in zero_cost_apps),
]
failed=[name for name, ok in required if not ok]
if failed:
    print('authority-boundary FAIL: ' + '; '.join(failed))
    sys.exit(1)
print('authority-boundary: PASS')
