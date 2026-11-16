"""GD-006 step 5: owner-only local queue, result files and runtime switch.

Layout under one owner-only root (default ``~/Local/Projeler/EJS/engine-queue``)::

    inbox/        Claude's EJS-EXECUTION-MANIFEST-1 files (``*.json``, 0600)
    processing/   claimed by an atomic rename; one engine at a time (``engine.lock``)
    outbox/       EJS-ENGINE-RESULT-1 files for Claude (``<execution_id>.result.json``)
    archive/      processed manifests
    control/      ``runtime.json`` and the ``STOP`` file

Runtime switch (safe by default):
  * ``control/STOP`` exists (any content)          -> kill switch engaged, no submit
  * ``control/runtime.json`` missing or invalid    -> final submit disabled
  * only ``{"schema_version": "EJS-ENGINE-RUNTIME-1", "final_submit": true}`` enables it

Every directory must be 0700 and every file 0600, owned by the current user, no
symlinks. A file that fails these checks is never read. Results carry fixed codes,
the observed next-page structure only, and never candidate values or CV bytes.
A manifest left in ``processing/`` by a crash is never re-run: it gets an
``INTERRUPTED`` result (``SUBMITTED_UNCONFIRMED`` if the ledger shows it was
dispatched) and is archived; Claude decides on any new attempt.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable

from ejs.persistence.policy_submission_ledger import PolicySubmissionLedger
from ejs.services.policy_application_runtime import PolicyApplicationRuntime, RunResult
from ejs.services.policy_submission import RuntimeSwitch

RESULT_SCHEMA = "EJS-ENGINE-RESULT-1"
RUNTIME_SCHEMA = "EJS-ENGINE-RUNTIME-1"
SUBDIRS = ("inbox", "processing", "outbox", "archive", "control")
MAX_MANIFEST_BYTES = 512_000
MAX_ITEMS_PER_RUN = 200
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.json\Z")


class QueueError(PermissionError):
    """Fixed-code refusal; never carries paths' contents or candidate values."""


def _require(condition: object, code: str = "ENGINE_QUEUE_UNSAFE") -> None:
    if not condition:
        raise QueueError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _owner_only(path: Path, *, directory: bool) -> os.stat_result:
    info = path.lstat()
    kind_ok = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = 0o700 if directory else 0o600
    _require(kind_ok and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == mode and (directory or info.st_nlink == 1))
    return info


def prepare_root(root: Path) -> None:
    """Create the owner-only layout (idempotent). Existing unsafe folders fail closed."""
    _require(isinstance(root, Path) and root.is_absolute())
    for component in (root, *root.parents):
        _require(not component.is_symlink())
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _owner_only(root, directory=True)
    for name in SUBDIRS:
        sub = root / name
        if not sub.exists():
            sub.mkdir(mode=0o700)
            os.chmod(sub, 0o700)
        _owner_only(sub, directory=True)


def _read_private(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                 and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= limit)
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(limit + 1)
        _require(len(data) <= limit)
        return data
    finally:
        if fd is not None:
            os.close(fd)


def _write_private(path: Path, payload: bytes) -> None:
    """Atomic owner-only write: temp file in the same folder, fsync, rename."""
    temp = path.with_name("." + path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)


def read_switch(root: Path) -> RuntimeSwitch:
    """Safe defaults: anything unexpected means no submission."""
    control = root / "control"
    try:
        _owner_only(control, directory=True)
        if os.path.lexists(control / "STOP"):
            return RuntimeSwitch(final_submit_enabled=False, kill_switch_engaged=True)
        data = json.loads(_read_private(control / "runtime.json", 4096))
        if (type(data) is dict and set(data) == {"schema_version", "final_submit"}
                and data["schema_version"] == RUNTIME_SCHEMA and data["final_submit"] is True):
            return RuntimeSwitch(final_submit_enabled=True, kill_switch_engaged=False)
    except Exception:
        pass
    return RuntimeSwitch()


@contextmanager
def _engine_lock(root: Path):
    fd = os.open(root / "engine.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and stat.S_IMODE(info.st_mode) == 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise QueueError("ENGINE_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ItemReport:
    name: str
    status: str
    code: str = ""


def _result_document(*, execution_id: str, manifest_sha256: str, result: RunResult, now: datetime) -> dict:
    observation = None
    if result.observation is not None:
        observation = dict(step=result.observation["step"], final=result.observation["final"],
                           fields=[dict(row) for row in result.observation["fields"]])
    return dict(schema_version=RESULT_SCHEMA, execution_id=execution_id, manifest_sha256=manifest_sha256,
                status=result.status, code=result.code, submit_clicks=result.submit_clicks,
                evidence_ref=result.evidence_ref or None, observation=observation,
                finished_at=now.astimezone(timezone.utc).isoformat())


def _execution_id(raw: bytes) -> str | None:
    try:
        value = json.loads(raw).get("execution_id")
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,199}", value) else None
    except Exception:
        return None


def _result_name(execution_id: str | None, digest: str) -> str:
    base = execution_id.replace(":", "_") if execution_id else "unidentified-" + digest[:16]
    return base + ".result.json"


class EngineQueue:
    def __init__(self, root: Path, *, ledger: PolicySubmissionLedger, cv_dir: Path,
                 browser_factory: Callable[[dict], object], clock=_utc_now):
        """``browser_factory(document)`` gets the parsed (still unvalidated) manifest so it
        can pin its network boundary to the target; the runtime validates it fully."""
        _require(type(ledger) is PolicySubmissionLedger and isinstance(cv_dir, Path) and callable(browser_factory))
        prepare_root(root)
        self.root, self.ledger, self.cv_dir = root, ledger, cv_dir
        self.browser_factory, self.clock = browser_factory, clock

    def _emit(self, raw: bytes, result: RunResult) -> None:
        digest = hashlib.sha256(raw).hexdigest()
        execution_id = _execution_id(raw)
        document = _result_document(execution_id=execution_id or "", manifest_sha256=digest,
                                    result=result, now=self.clock())
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        target = self.root / "outbox" / _result_name(execution_id, digest)
        _require(not os.path.lexists(target), "ENGINE_RESULT_EXISTS")
        _write_private(target, payload)

    def _archive(self, path: Path) -> None:
        target = self.root / "archive" / path.name
        if os.path.lexists(target):
            target = self.root / "archive" / (path.stem + "." + hashlib.sha256(path.name.encode()
                                              + str(self.clock()).encode()).hexdigest()[:8] + ".json")
        os.replace(path, target)

    def _recover(self) -> list[ItemReport]:
        """Never re-run an interrupted item; report it once."""
        reports = []
        for path in sorted((self.root / "processing").iterdir()):
            raw = b""
            status = "INTERRUPTED"
            try:
                raw = _read_private(path, MAX_MANIFEST_BYTES)
                document = json.loads(raw)  # ids only; an expired manifest must still be reported
                state = self.ledger.status(document["target"]["opportunity_key"],
                                           document["target"]["requisition_id"],
                                           document["candidate_profile_version"])
                if state in ("Submitted / Unconfirmed", "Confirmed"):
                    status = "SUBMITTED_UNCONFIRMED" if state != "Confirmed" else "EMPLOYER_CONFIRMED"
            except Exception:
                pass  # expired or unreadable: still reported as INTERRUPTED
            result = RunResult(status, "ENGINE_INTERRUPTED", submit_clicks=-1 if status != "INTERRUPTED" else 0)
            try:
                self._emit(raw or path.name.encode(), result)
            except QueueError:
                pass
            self._archive(path)
            reports.append(ItemReport(path.name, status, "ENGINE_INTERRUPTED"))
        return reports

    def run_once(self) -> list[ItemReport]:
        """Process every inbox manifest once, in name order. Returns codes only."""
        with _engine_lock(self.root):
            reports = self._recover()
            switch = read_switch(self.root)
            inbox = sorted(p for p in (self.root / "inbox").iterdir() if not p.name.startswith("."))
            for path in inbox[:MAX_ITEMS_PER_RUN]:
                if not _NAME.fullmatch(path.name):
                    continue  # never touched
                claimed = self.root / "processing" / path.name
                try:
                    _owner_only(path, directory=False)
                    os.rename(path, claimed)  # atomic claim
                except (OSError, QueueError):
                    reports.append(ItemReport(path.name, "SKIPPED_UNSAFE", "ENGINE_QUEUE_UNSAFE"))
                    continue
                reports.append(self._process(claimed, switch))
                switch = read_switch(self.root)  # STOP takes effect between items
            return reports

    def _process(self, path: Path, switch: RuntimeSwitch) -> ItemReport:
        try:
            raw = _read_private(path, MAX_MANIFEST_BYTES)
        except Exception:
            result = RunResult("BLOCKED", "ENGINE_MANIFEST_UNREADABLE")
            self._emit(path.name.encode(), result)
            self._archive(path)
            return ItemReport(path.name, result.status, result.code)
        digest = hashlib.sha256(raw).hexdigest()
        cv_name, document = None, {}
        try:
            document = json.loads(raw)
            cv_name = document["cv"]["filename"]
        except Exception:
            pass
        cv_path = self.cv_dir / cv_name if isinstance(cv_name, str) and "/" not in cv_name else self.cv_dir / "-"
        browser = None
        try:
            browser = self.browser_factory(document)
            result = PolicyApplicationRuntime(browser, self.ledger, clock=self.clock).run(
                raw, expected_sha256=digest, cv_path=cv_path, switch=switch)
        except Exception:
            result = RunResult("BLOCKED", "ENGINE_BROWSER_UNAVAILABLE")
        finally:
            close = getattr(browser, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._emit(raw, result)
        self._archive(path)
        return ItemReport(path.name, result.status, result.code)
