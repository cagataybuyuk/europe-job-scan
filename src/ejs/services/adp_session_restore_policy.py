"""Target-bound sessionStorage restoration for existing diagnostic replay only."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_same_page_manifest import REVIEWED_POSTLOGIN_PATH

ENTRY_PATH = '/mascsr/default/mdf/recruitment/recruitment.html'


def session_restore_script(application_url: str, storage: dict[str, str]) -> str:
    validate_adp_live_url(application_url)
    parsed = urlsplit(application_url)
    if parsed.port not in (None, 443) or parsed.path not in {ENTRY_PATH, REVIEWED_POSTLOGIN_PATH}:
        raise ValueError('ADP_SESSION_RESTORE_TARGET_INVALID')
    parameters: dict[str, list[str]] = {}
    for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
        parameters.setdefault(key.casefold(), []).extend(values)
    target = {}
    for key in ('cid', 'ccid', 'jobid'):
        values = parameters.get(key, [])
        if len(values) != 1 or not values[0]:
            raise ValueError('ADP_SESSION_RESTORE_TARGET_INVALID')
        target[key] = values[0]
    if not isinstance(storage, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in storage.items()):
        raise ValueError('ADP_SESSION_RESTORE_STORAGE_INVALID')
    return (
        '(() => {'
        'if (window.self !== window.top) return;'
        f'if (window.location.origin !== {json.dumps("https://" + parsed.hostname)}) return;'
        f'if (!{json.dumps([ENTRY_PATH, REVIEWED_POSTLOGIN_PATH])}.includes(window.location.pathname)) return;'
        f'const expected = {json.dumps(target, sort_keys=True)};'
        'const actual = {};'
        'for (const [key, value] of new URLSearchParams(window.location.search)) {'
        'const normalized = key.toLowerCase();'
        'if (!Object.hasOwn(actual, normalized)) actual[normalized] = [];'
        'actual[normalized].push(value);'
        '}'
        'for (const [key, value] of Object.entries(expected)) {'
        'if (!actual[key] || actual[key].length !== 1 || actual[key][0] !== value) return;'
        '}'
        f'const restored = {json.dumps(storage, ensure_ascii=False, sort_keys=True)};'
        'for (const [key, value] of Object.entries(restored)) window.sessionStorage.setItem(key, value);'
        '})();'
    )
