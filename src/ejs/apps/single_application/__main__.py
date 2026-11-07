"""Validate private pins by default; execute only the scoped first application.

No environment flag, cloud deployment or legacy canary is enabled. The canonical
state path is fixed for this host and cannot be selected by a job/manifest/CLI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import subprocess

from ejs.contracts.submit import SubmitAuthority, submit_identity
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.application_outcome import write_confirmed_outcome
from ejs.services.oneclick_browser import OneClickBrowser
from ejs.services.oneclick_target import OneClickTarget, OneClickNetworkBoundary
from ejs.services.single_application_inputs import load_reviewed_application_inputs
from ejs.services.single_application_runtime import SingleApplicationRuntime, read_approved_cv, require


# This is the already established first-live ledger. Never derive it from a
# worktree, execution/profile version or posting, and never add a --ledger flag.
# The OS account database, unlike Path.home(), cannot be redirected by a
# caller-supplied HOME environment and reopen the first-application budget.
_STATE_ROOT = Path(pwd.getpwuid(os.getuid()).pw_dir) / 'Local' / 'Projeler' / 'EJS' / '.ejs-local'
_LEDGER = _STATE_ROOT / 'first-application.sqlite3'
_OUTCOME = _STATE_ROOT / 'first-application-outcome.json'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Validate independently pinned first-application inputs; default performs no browser action.')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--facts', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--facts-sha256', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--cv', type=Path, required=True)
    parser.add_argument('--execute-reviewed-first-application', action='store_true')
    args = parser.parse_args(argv)
    result = None
    try:
        repo = Path(__file__).resolve().parents[4]
        actual = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True,
                                capture_output=True, text=True).stdout.strip()
        clean = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=all'],
                               check=True, capture_output=True, text=True).stdout
        require(actual == args.source_sha and not clean, 'APPLICATION_SOURCE_REVIEW_REQUIRED')
        manifest, facts = load_reviewed_application_inputs(args.manifest, args.facts,
            manifest_sha256=args.manifest_sha256, facts_sha256=args.facts_sha256,
            source_sha=args.source_sha, now=datetime.now(timezone.utc))
        target = OneClickTarget(**manifest['target'])
        require((target.company, target.company_id, target.posting_id, target.publication_id, target.requisition_id) ==
                ('Version1', '593fe2a3e4b0ac9d00fb37f8', '744000159638089',
                 'aaf6587c-8336-4454-adb1-e09661d9bdfd', 'REF7089X'),
                'APPLICATION_FIRST_TARGET_REVIEW_REQUIRED')
        read_approved_cv(args.cv, manifest['cv'])
        if not args.execute_reviewed_first_application:
            print(json.dumps({'status': 'INPUTS_VALIDATED', 'browser_action_performed': False,
                              'submit_authorized': False}))
            return 0
        # Check the one canonical budget before creating a browser context.
        ledger = SqliteSubmissionLedger(_LEDGER)
        if ledger.has_intent():
            print(json.dumps({'status': 'DUPLICATE_OR_UNCERTAIN', 'browser_action_performed': False, 'applied': False}))
            return 2
        boundary = OneClickNetworkBoundary(target, read_urls=tuple(manifest['read_urls']),
                                           resume_filename=manifest['cv']['approved_filename'])
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(service_workers='block', accept_downloads=False)
            try:
                driver = OneClickBrowser(context, boundary, manifest)
                result = SingleApplicationRuntime(driver, ledger).run(manifest, facts, target, args.cv,
                    authority=SubmitAuthority(browser_external_form_write=True,
                        final_submit_runtime_flag=True, kill_switch_engaged=False))
                if result['status'] == 'EMPLOYER_CONFIRMED':
                    try:
                        outcome = write_confirmed_outcome(_OUTCOME, ledger=ledger,
                            submit_key=submit_identity(manifest['opportunity_key'], target.requisition_id, manifest['candidate_profile_version']),
                            execution_id=manifest['execution_id'], requisition_id=target.requisition_id,
                            posting_url=target.posting_url, evidence_ref=result['evidence_ref'])
                        result['local_outcome_write_performed'] = outcome['local_outcome_write_performed']
                    except Exception:
                        # A local reporting failure cannot undo the authenticated
                        # employer result or make a second dispatch permissible.
                        result['local_outcome_write_performed'] = False
                        result['outcome_record_pending'] = True
            finally:
                # Cleanup must not replace a confirmed result with BLOCKED or
                # emit a second contradictory terminal report after submission.
                for resource in (context, browser):
                    try:
                        resource.close()
                    except Exception:
                        pass
    except Exception:
        # Never leak filenames, candidate data, HTTP bodies or library tracebacks.
        if result is None:
            result = {'status': 'BLOCKED', 'applied': False}
        else:
            result['cleanup_incomplete'] = True
    # Emit only after the Playwright context manager itself has also stopped.
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'EMPLOYER_CONFIRMED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
