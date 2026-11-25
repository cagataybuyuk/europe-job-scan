"""Run a bounded, owner-only SmartRecruiters preparation pilot."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from time import monotonic, sleep
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from shadow_discovery import private_output_directory, require_no_extended_acl

from ejs.apps.reviewed_application.__main__ import _LEDGER
from ejs.persistence.policy_submission_ledger import read_consumed_requisitions, requisition_hashes
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.application_batch import new_state, run_application_batch
from ejs.services.application_bridge import MAX_DETAIL_BYTES
from ejs.services.smartrecruiters_live_inspector import inspect_live_page

_STATE = "retry-state.json"


@contextmanager
def private_file(directory_fd, name, flags):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("batch file must be owner-only")
        require_no_extended_acl(fd)
        yield fd
    finally:
        os.close(fd)


def persist(directory_fd, name, value, *, replace=False):
    pending = f".batch-{uuid4().hex}.tmp"
    created = False
    try:
        with private_file(directory_fd, pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL) as fd:
            created = True
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(fd)
        if replace:
            os.replace(pending, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        else:
            os.link(pending, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
    finally:
        if created:
            try:
                os.unlink(pending, dir_fd=directory_fd)
            except FileNotFoundError:
                # os.replace has already moved the temporary inode.
                pass
    os.fsync(directory_fd)


def load_exclusions(path: Path | None, stack: ExitStack) -> tuple[set, set]:
    postings, requisitions = set(), set()
    if path is None:
        return postings, requisitions
    if not path.parent.is_dir():
        raise ValueError("exclusions directory must exist")
    directory_fd = private_output_directory(path.parent, stack)
    with private_file(directory_fd, path.name, os.O_RDONLY) as fd:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("batch exclusions too large")
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError("invalid exclusions")
    for row in rows:
        if (not isinstance(row, dict) or not {"tenant", "requisition_id"} <= set(row)
                or set(row) - {"tenant", "requisition_id", "posting_id"}
                or not all(isinstance(v, str) for v in row.values())
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", row["tenant"])
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", row["requisition_id"])
                or ("posting_id" in row and not re.fullmatch(r"[0-9]{6,20}", row["posting_id"]))):
            raise ValueError("invalid exclusion identity")
        tenant = row["tenant"].casefold()
        if "posting_id" in row:
            postings.add((tenant, row["posting_id"]))
        requisitions.update((tenant, digest) for digest in requisition_hashes(row["requisition_id"]))
    return postings, requisitions


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def live_detail_transport():
    opener = build_opener(ProxyHandler({}), NoRedirect())
    next_read_at = 0.0

    def read(url):
        nonlocal next_read_at
        if not re.fullmatch(r"https://api\.smartrecruiters\.com/v1/companies/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/postings/[0-9]{6,20}", url):
            raise ValueError("unverified detail URL")
        sleep(max(next_read_at - monotonic(), 0))
        try:
            request = Request(url, headers={"Accept": "application/json"}, method="GET")
            with opener.open(request, timeout=10) as response:
                return response.read(MAX_DETAIL_BYTES + 1)
        finally:
            next_read_at = monotonic() + 2

    return read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discovery", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--exclusions", type=Path, required=True,
                        help="owner-only JSON list; [] explicitly declares no private exclusions")
    args = parser.parse_args()
    if not args.live_read_only or not 1 <= args.budget <= 5:
        parser.error("pilot requires --live-read-only and a vacancy budget of 1–5")
    with ExitStack() as stack:
        directory_fd = private_output_directory(args.output_directory, stack)
        lock = stack.enter_context(private_file(directory_fd, ".batch.lock", os.O_CREAT | os.O_RDWR))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        postings, requisitions = load_exclusions(args.exclusions, stack)
        if not args.discovery.parent.is_dir():
            raise ValueError("discovery directory must exist")
        input_fd = private_output_directory(args.discovery.parent, stack)
        with private_file(input_fd, args.discovery.name, os.O_RDONLY) as fd:
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(32_000_001)
        if len(raw) > 32_000_000:
            raise ValueError("discovery report too large")
        discovery = json.loads(raw)
        try:
            with private_file(directory_fd, _STATE, os.O_RDONLY) as fd:
                with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
                    raw_state = stream.read(32_000_001)
                    if len(raw_state) > 32_000_000:
                        raise ValueError("retry state too large")
                    state = json.loads(raw_state)
        except FileNotFoundError:
            state = new_state()
        ledger = object.__new__(SqliteSubmissionLedger)
        ledger.path = _LEDGER
        read_consumed_requisitions(ledger)  # fail closed before any network reads
        transport = live_detail_transport()

        def checkpoint(value):
            persist(directory_fd, _STATE, value, replace=True)

        report = run_application_batch(discovery, state, budget=args.budget, detail=transport,
            inspect=inspect_live_page, consumed_requisitions=lambda: read_consumed_requisitions(ledger),
            checkpoint=checkpoint, excluded_postings=postings, excluded_requisitions=requisitions)
        name = f"batch-{uuid4().hex}.json"
        persist(directory_fd, name, report)
    print(json.dumps({"attempted_vacancies": report["attempted_vacancies"], "report": name}))


if __name__ == "__main__":
    main()
