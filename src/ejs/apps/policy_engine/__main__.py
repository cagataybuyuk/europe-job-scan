"""GD-006 engine entry point (launchd runs this on the owner's Mac, step 6).

    python -m ejs.apps.policy_engine            # process the queue once
    python -m ejs.apps.policy_engine --prepare  # only create the owner-only folders
    python -m ejs.apps.policy_engine --launchd-plist  # print the 08:30 LaunchAgent (installs nothing)

Fixed private locations under ~/Local/Projeler/EJS; no path, URL or authority is
read from arguments or the environment. The runtime switch lives in
engine-queue/control (see docs/ENGINE_QUEUE.md). Output: status codes only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import plistlib
import json
import os
from pathlib import Path
import pwd
import sys

_BASE = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Local" / "Projeler" / "EJS"
QUEUE_ROOT = _BASE / "engine-queue"
CV_DIR = _BASE / "claude-applications"
LAUNCHD_LABEL = "com.ejs.policy-engine"


def launchd_plist(python: str) -> bytes:
    """The LaunchAgent for 08:30 Europe/Istanbul (the Mac's local time). Nothing is installed."""
    if not (isinstance(python, str) and python.startswith("/") and "\n" not in python):
        raise PermissionError("ENGINE_PYTHON_PATH_INVALID")
    log = str(QUEUE_ROOT / "logs" / "engine.log")
    return plistlib.dumps({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [python, "-I", "-B", "-m", "ejs.apps.policy_engine"],
        "StartCalendarInterval": {"Hour": 8, "Minute": 30},
        "RunAtLoad": False,
        "KeepAlive": False,
        "ProcessType": "Interactive",  # visible browser: the owner may solve a challenge
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "Umask": 0o077,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process the EJS engine queue once.")
    parser.add_argument("--prepare", action="store_true", help="create the owner-only queue folders and exit")
    parser.add_argument("--launchd-plist", action="store_true",
                        help="print the 08:30 LaunchAgent plist for this interpreter; installs nothing")
    args = parser.parse_args(argv)
    if args.launchd_plist:
        sys.stdout.write(launchd_plist(sys.executable).decode("utf-8"))
        return 0

    from ejs.services.engine_queue import EngineQueue, prepare_root
    prepare_root(QUEUE_ROOT)
    if args.prepare:
        print(json.dumps({"status": "PREPARED"}))
        return 0

    from ejs.services.engine_queue import read_switch, switch_enabled
    if not switch_enabled(read_switch(QUEUE_ROOT)):
        # No browser is opened and the inbox is left untouched while submission is off.
        print(json.dumps({"status": "DISABLED"}))
        return 0

    from playwright.sync_api import sync_playwright

    from ejs.apps.reviewed_application.__main__ import open_existing_canonical_ledger
    from ejs.persistence.policy_submission_ledger import enable_policy_submissions
    from ejs.services.smartrecruiters_policy_browser import PolicyNetworkBoundary, SmartRecruitersPolicyBrowser

    ledger = enable_policy_submissions(open_existing_canonical_ledger(), now=datetime.now(timezone.utc))
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=False)  # visible: the owner may solve a challenge
        try:
            def factory(document: dict):
                target = document["target"]
                if target.get("ats_family") != "smartrecruiters":
                    raise PermissionError("ENGINE_ATS_UNSUPPORTED")
                boundary = PolicyNetworkBoundary(target["company"], target["posting_id"],
                                                 document["cv"]["filename"])
                return SmartRecruitersPolicyBrowser.launch(chromium, boundary)

            reports = EngineQueue(QUEUE_ROOT, ledger=ledger, cv_dir=CV_DIR, browser_factory=factory).run_once()
        finally:
            chromium.close()
    print(json.dumps({"status": "DONE", "items": [{"status": r.status, "code": r.code} for r in reports]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
