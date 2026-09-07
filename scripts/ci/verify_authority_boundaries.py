from pathlib import Path
import sys

root=Path(__file__).resolve().parents[2]
contract=(root/'src/ejs/contracts/google_native.py').read_text()
apps='\n'.join(p.read_text() for p in (root/'google_native/apps_script').glob('*.gs'))
required=[
    ('google-native contract forbids form write', 'form_write' in contract and 'PermissionError' in contract),
    ('google-native contract forbids file upload', 'file_upload' in contract and 'PermissionError' in contract),
    ('google-native contract forbids final submit', 'final_submit' in contract and 'PermissionError' in contract),
    ('Apps Script TEST target present', '17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM' in apps),
    ('Apps Script PROD deny target present', '1_HuYScTMsVmr29SBeaMRbZqOFiZ3gZLUcFm05xnaYIg' in apps),
]
failed=[name for name, ok in required if not ok]
if failed:
    print('authority-boundary FAIL: ' + '; '.join(failed))
    sys.exit(1)
print('authority-boundary: PASS')
