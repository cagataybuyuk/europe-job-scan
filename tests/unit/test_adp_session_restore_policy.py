import json
import shutil
import subprocess
import unittest

from ejs.services.adp_session_restore_policy import session_restore_script, ENTRY_PATH
from ejs.services.adp_same_page_manifest import REVIEWED_POSTLOGIN_PATH
from ejs.services.adp_verified_session_bootstrap import _session_storage_restore_script
from ejs.services.adp_verified_session_inspector import _session_storage_init_script

URL = 'https://workforcenow.adp.com' + ENTRY_PATH + '?cid=test&ccId=one&jobId=123'


class SessionRestorePolicyTests(unittest.TestCase):
    def test_both_existing_replay_callers_use_identical_policy(self):
        expected = session_restore_script(URL, {'synthetic': 'fixture'})
        self.assertEqual(_session_storage_restore_script(URL, {'synthetic': 'fixture'}), expected)
        self.assertEqual(_session_storage_init_script(URL, {'synthetic': 'fixture'}), expected)

    def test_ambiguous_or_unreviewed_expected_targets_are_rejected(self):
        for url in (URL + '&CID=test', URL.replace('cid=test', 'cid='),
                    URL.replace('jobId=123', 'other=123'), URL.replace('.com/', '.com:444/'),
                    URL.replace(ENTRY_PATH, '/unreviewed')):
            with self.subTest(url=url), self.assertRaises(ValueError):
                session_restore_script(url, {})

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the actual JavaScript policy test')
    def test_actual_script_restores_only_exact_top_level_https_target(self):
        script = session_restore_script(URL, {'synthetic-key': 'synthetic-value'})
        harness = r'''
const vm = require('node:vm');
const payload = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const results = payload.cases.map(c => {
 const url = new URL(c.url), writes = [];
 const window = {location:url, sessionStorage:{setItem:(k,v)=>writes.push([k,v])}};
 window.self = window; window.top = c.frame ? {} : window;
 vm.runInNewContext(payload.script, {window, URLSearchParams});
 return writes;
});
process.stdout.write(JSON.stringify(results));
'''
        cases = [
            (URL, False, True),
            (URL.replace(ENTRY_PATH, REVIEWED_POSTLOGIN_PATH), False, True),
            (URL.replace('ccId', 'CCID') + '&lang=en', False, True),
            (URL.replace('https:', 'http:'), False, False),
            (URL.replace('.com/', '.com:444/'), False, False),
            (URL.replace('workforcenow.adp.com', 'example.invalid'), False, False),
            (URL.replace(ENTRY_PATH, '/unreviewed'), False, False),
            (URL.replace('cid=test', 'cid=other'), False, False),
            (URL.replace('jobId=123', 'jobId=456'), False, False),
            (URL.replace('ccId=one&', ''), False, False),
            (URL + '&CID=test', False, False),
            (URL.replace('jobId=123', 'jobId='), False, False),
            (URL, True, False),
        ]
        result = subprocess.run([shutil.which('node'), '-e', harness],
            input=json.dumps({'script': script, 'cases': [{'url': u, 'frame': f} for u, f, _ in cases]}),
            text=True, capture_output=True, check=True, timeout=10)
        writes = json.loads(result.stdout)
        for (url, frame, allowed), observed in zip(cases, writes, strict=True):
            with self.subTest(url=url, frame=frame):
                self.assertEqual(observed, [['synthetic-key', 'synthetic-value']] if allowed else [])
