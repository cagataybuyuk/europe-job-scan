"""Validate the exact next target by default; explicit private operation grant required."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

from ejs.contracts.submit import SubmitAuthority, submit_identity
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.persistence.reviewed_scope_ledger import (
    claim_reviewed_preparation, register_reviewed_execution_scope,
)
from ejs.services.application_outcome import write_confirmed_outcome
from ejs.services.oneclick_browser import OneClickBrowser
from ejs.services.oneclick_target import OneClickTarget, native_entry_policy_enabled, reviewed_oneclick_boundary
from ejs.services.reviewed_execution_scope import EXPECTED_FUTURE_DECISION_REF, review_execution_scope
from ejs.services.single_application_inputs import load_reviewed_application_inputs
from ejs.services.single_application_runtime import SingleApplicationRuntime, read_approved_cv, require


_STATE_ROOT = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Local" / "Projeler" / "EJS" / ".ejs-local"
_LEDGER = _STATE_ROOT / "first-application.sqlite3"
_OUTCOME = _STATE_ROOT / "devoteam-ref00063-outcome.json"
# Public logo company ID is still a separately reviewed backend-binding
# prerequisite. This constant and expected decision string are not a live grant.
_TARGET = OneClickTarget(company="Devoteam", company_id="5b8f87c1e4b07eb5549de6e8",
    posting_id="744000156831399", publication_id="3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e",
    requisition_id="REF00063",
    posting_url="https://jobs.smartrecruiters.com/Devoteam/744000156831399-digital-impulse-business-analyst-")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_STATUSES = frozenset({"INPUTS_VALIDATED", "BLOCKED", "CANCELLED", "RUNTIME_DISABLED",
    "DUPLICATE_OR_UNCERTAIN", "SUBMITTED_UNCONFIRMED", "HUMAN_HANDOFF_REQUIRED", "EMPLOYER_CONFIRMED"})


def _now():
    return datetime.now(timezone.utc)


def _private_parents(path: Path) -> None:
    require(isinstance(path, Path) and path.is_absolute() and ".." not in path.parts,
            "APPLICATION_PRIVATE_INPUT_REQUIRED")
    for component in (path.parent, *path.parent.parents):
        info = component.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
                "APPLICATION_PRIVATE_INPUT_REQUIRED")
    info = path.parent.lstat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "APPLICATION_PRIVATE_INPUT_REQUIRED")


def read_private_pinned_bytes(path: Path, expected_hash: str) -> bytes:
    """Read the actual checked owner-only inode, without a second unchecked read."""
    parent_fd = fd = None
    try:
        _private_parents(path)
        require(type(expected_hash) is str and _HEX64.fullmatch(expected_hash) is not None,
                "APPLICATION_PRIVATE_INPUT_REQUIRED")
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(parent_fd)
        current = path.parent.lstat()
        require((parent.st_dev, parent.st_ino) == (current.st_dev, current.st_ino)
                and parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
                "APPLICATION_PRIVATE_INPUT_REQUIRED")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= 1_000_000, "APPLICATION_PRIVATE_INPUT_REQUIRED")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            payload = stream.read(1_000_001)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and len(payload) <= 1_000_000
                and hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_hash),
                "APPLICATION_PRIVATE_INPUT_REQUIRED")
        _private_parents(path)
        current = path.parent.lstat()
        require((parent.st_dev, parent.st_ino) == (current.st_dev, current.st_ino),
                "APPLICATION_PRIVATE_INPUT_REQUIRED")
        return payload
    except Exception:
        raise PermissionError("APPLICATION_PRIVATE_INPUT_REQUIRED") from None
    finally:
        if fd is not None:
            os.close(fd)
        if parent_fd is not None:
            os.close(parent_fd)


def open_existing_canonical_ledger() -> SqliteSubmissionLedger:
    """Use mode=rw checks on existing fixed state; never the constructor/create path."""
    _private_parents(_LEDGER)
    info = _LEDGER.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size > 0,
            "APPLICATION_CANONICAL_HISTORY_REQUIRED")
    # SqliteSubmissionLedger initializes new files in __init__. A fixed existing
    # view needs only path; its strict mode=rw transaction validates all schema
    # and durable state, and cannot create a deleted/missing file.
    ledger = object.__new__(SqliteSubmissionLedger)
    ledger.path = _LEDGER
    ledger._check_file()
    ledger.snapshot()
    return ledger


def _report(result: dict) -> dict:
    """Keep stdout to known terminal statuses/counts/flags, never private values."""
    status = result.get("status")
    status = status if status in _STATUSES else "BLOCKED"
    report = {"status": status, "applied": result.get("applied") is True and status == "EMPLOYER_CONFIRMED",
              "applications_write_performed": False}
    for key in ("browser_action_performed", "submit_authorized", "local_outcome_write_performed",
                "outcome_record_pending", "central_record_pending", "preparation_scope_consumed",
                "cleanup_incomplete", "dispatch_intent_recorded", "fresh_return_review_required",
                "native_fallback_allowed"):
        if type(result.get(key)) is bool:
            report[key] = result[key]
    attempts = result.get("submit_click_attempts")
    if attempts is None or type(attempts) is int and attempts in (0, 1):
        report["submit_click_attempts"] = attempts
    code = result.get("error_code")
    if type(code) is str and re.fullmatch(r"(?:APPLICATION|ONECLICK)_[A-Z0-9_]{1,100}", code):
        report["error_code"] = code
    return report


def _recover(view, *, claim_started: bool = False) -> dict:
    """Never let cleanup/cancellation hide an already durable employer result."""
    try:
        snapshot = view.snapshot() if view is not None else None
        if snapshot and snapshot["status"] == "Confirmed":
            return {"status": "EMPLOYER_CONFIRMED", "applied": True, "submit_click_attempts": 1,
                    "local_outcome_write_performed": False, "outcome_record_pending": True,
                    "central_record_pending": True, "cleanup_incomplete": True}
        if snapshot and snapshot["status"] == "Submitted / Unconfirmed":
            return {"status": "SUBMITTED_UNCONFIRMED", "applied": False,
                    "submit_click_attempts": None, "dispatch_intent_recorded": True}
    except Exception:
        pass
    return {"status": "BLOCKED", "applied": False,
            "preparation_scope_consumed": view is not None or claim_started}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the reviewed Devoteam REF00063 target; default performs no database/browser action.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--facts-sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--cv", type=Path, required=True)
    parser.add_argument("--execute-reviewed-application", action="store_true")
    parser.add_argument("--grant", type=Path)
    parser.add_argument("--grant-sha256")
    parser.add_argument("--user-decision-ref")
    args = parser.parse_args(argv)
    result = view = None
    claim_started = False
    try:
        repo = Path(__file__).resolve().parents[4]
        actual = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        clean = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                               check=True, capture_output=True, text=True, timeout=10).stdout
        require(actual == args.source_sha and not clean, "APPLICATION_SOURCE_REVIEW_REQUIRED")
        manifest, facts = load_reviewed_application_inputs(args.manifest, args.facts,
            manifest_sha256=args.manifest_sha256, facts_sha256=args.facts_sha256,
            source_sha=args.source_sha, now=_now())
        target = OneClickTarget(**manifest["target"])
        require(target == _TARGET and native_entry_policy_enabled(manifest), "APPLICATION_NEXT_TARGET_REVIEW_REQUIRED")
        read_approved_cv(args.cv, manifest["cv"])
        if not args.execute_reviewed_application:
            result = {"status": "INPUTS_VALIDATED", "browser_action_performed": False,
                      "submit_authorized": False, "applied": False}
        else:
            require(args.user_decision_ref == EXPECTED_FUTURE_DECISION_REF
                    and args.grant is not None and args.grant_sha256 is not None,
                    "APPLICATION_EXACT_OPERATION_GRANT_REQUIRED")
            grant_payload = read_private_pinned_bytes(args.grant, args.grant_sha256)
            manifest_payload = read_private_pinned_bytes(args.manifest, args.manifest_sha256)
            facts_payload = read_private_pinned_bytes(args.facts, args.facts_sha256)
            legacy = open_existing_canonical_ledger()
            scope = review_execution_scope(grant_payload, manifest_payload, facts_payload,
                grant_sha256=args.grant_sha256, manifest_sha256=args.manifest_sha256,
                facts_sha256=args.facts_sha256, source_sha=args.source_sha,
                expected_decision_ref=args.user_decision_ref, expected_target=_TARGET,
                legacy_snapshot=legacy.snapshot(), now=_now())
            register_reviewed_execution_scope(legacy, scope, now=_now())
            claim_started = True
            view = claim_reviewed_preparation(legacy, scope, now=_now())
            if view is None:
                result = {"status": "DUPLICATE_OR_UNCERTAIN", "browser_action_performed": False, "applied": False}
            else:
                boundary = reviewed_oneclick_boundary(target, manifest)
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser = context = None
                    try:
                        browser = playwright.chromium.launch(headless=False)
                        context = browser.new_context(service_workers="block", accept_downloads=False)
                        driver = OneClickBrowser(context, boundary, manifest)
                        runtime = SingleApplicationRuntime(driver, view)
                        authority = SubmitAuthority(browser_external_form_write=True,
                            final_submit_runtime_flag=True, kill_switch_engaged=False)
                        # Exactly one owned runtime invocation. A failed prepare
                        # is still consumed; no retry/resume/fallback call exists.
                        result = runtime.run(manifest, facts, target, args.cv, authority=authority)
                        result["preparation_scope_consumed"] = True
                        if result["status"] == "EMPLOYER_CONFIRMED":
                            result["central_record_pending"] = True
                            try:
                                outcome = write_confirmed_outcome(_OUTCOME, ledger=view,
                                    submit_key=submit_identity(manifest["opportunity_key"], target.requisition_id,
                                                               manifest["candidate_profile_version"]),
                                    execution_id=manifest["execution_id"], requisition_id=target.requisition_id,
                                    posting_url=target.posting_url, evidence_ref=result["evidence_ref"])
                                result["local_outcome_write_performed"] = outcome["local_outcome_write_performed"]
                            except (Exception, KeyboardInterrupt):
                                result["local_outcome_write_performed"] = False
                                result["outcome_record_pending"] = True
                    finally:
                        for resource in (context, browser):
                            if resource is not None:
                                try:
                                    resource.close()
                                except (Exception, KeyboardInterrupt):
                                    if result is not None:
                                        result["cleanup_incomplete"] = True
    except (Exception, KeyboardInterrupt):
        if result is None:
            result = _recover(view, claim_started=claim_started)
        else:
            result["cleanup_incomplete"] = True
    print(json.dumps(_report(result), sort_keys=True))
    return 0 if result["status"] in {"INPUTS_VALIDATED", "EMPLOYER_CONFIRMED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
