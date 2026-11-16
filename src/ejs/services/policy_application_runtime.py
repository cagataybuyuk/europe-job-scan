"""GD-006 step 4a: manifest-driven, policy-authorized application runtime.

Browser-agnostic orchestration of ONE prepared EJS-EXECUTION-MANIFEST-1:

    authorize (kill switch, flag, validator, reservation)
    -> open apply URL -> live form fingerprint == manifest fingerprint
    -> write every non-blank answer, read each back
    -> upload the approved CV (bytes verified against the manifest)
    -> full readback + fresh inspection
    -> CAPTCHA / MFA / login: pause, owner solves, verify again (never bypassed)
    -> mark_started (committed BEFORE the click) -> submit once
    -> exact confirmation -> ledger.confirm

Before ``mark_started`` every failure releases the reservation (no click happened).
After it, nothing is retried, released or replaced; an unverifiable outcome stays
``SUBMITTED_UNCONFIRMED``. Results carry fixed codes only, never candidate values.

The real SmartRecruiters Playwright binding of ``PolicyFormBrowser`` is step 4b.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Protocol

from ejs.contracts.submit import submit_identity
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType
from ejs.persistence.policy_submission_ledger import PolicySubmissionLedger
from ejs.services.execution_manifest import ExecutionManifest, ManifestField, form_fingerprint
from ejs.services.policy_submission import PolicyDecision, RuntimeSwitch, authorize_manifest

_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CODE = re.compile(r"POLICY_APP_[A-Z0-9_]{1,80}\Z")
MAX_CV_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class UploadResult:
    payload_sha256: str
    payload_size: int
    filename: str
    attachment_visible: bool


@dataclass(frozen=True)
class SubmitObservation:
    observed_at: datetime
    success_acknowledged: bool
    confirmation_visible: bool
    acknowledgement_sha256: str
    challenge_detected: bool


class PolicyFormBrowser(Protocol):
    """One owned page. ``inspect`` returns structure only:

    ``{"url": str, "fields": [{key,label,control,required,options}], "cv_upload": {key,label,required},
       "challenge": bool, "form_valid": bool, "submit_visible": bool,
       "cv_attachment": None | {"sha256": str, "size": int, "filename": str, "visible": bool}}``
    """

    def open(self, url: str) -> None: ...
    def inspect(self) -> dict: ...
    def write(self, key: str, control: str, value: object) -> None: ...
    def readback(self, key: str, control: str) -> object: ...
    def upload(self, key: str, payload: bytes, filename: str) -> UploadResult: ...
    def wait_for_owner(self, timeout_seconds: float) -> bool: ...
    def submit_once(self) -> SubmitObservation: ...


@dataclass(frozen=True)
class RunResult:
    status: str                  # EMPLOYER_CONFIRMED | SUBMITTED_UNCONFIRMED | BLOCKED | <PolicyDecision>
    code: str = ""
    submit_clicks: int = 0       # -1: unknown (started, outcome unverified)
    evidence_ref: str = ""

    @property
    def applied(self) -> bool:
        return self.status == "EMPLOYER_CONFIRMED"


class _Stop(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require(condition: object, code: str) -> None:
    if not condition:
        raise _Stop(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_cv(path: Path, manifest: ExecutionManifest) -> bytes:
    """Owner-only regular file whose bytes match the manifest (no symlink, no TOCTOU)."""
    fd = dfd = None
    try:
        _require(isinstance(path, Path) and path.name == manifest.cv_filename, "POLICY_APP_CV")
        for component in (path.parent, *path.parent.parents):
            _require(not component.is_symlink(), "POLICY_APP_CV")
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        folder = os.fstat(dfd)
        _require(folder.st_uid == os.getuid() and stat.S_IMODE(folder.st_mode) == 0o700, "POLICY_APP_CV")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                 and stat.S_IMODE(info.st_mode) == 0o600, "POLICY_APP_CV")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            payload = stream.read(MAX_CV_BYTES + 1)
        _require(payload.startswith(b"%PDF-") and len(payload) == manifest.cv_size_bytes
                 and hashlib.sha256(payload).hexdigest() == manifest.cv_sha256, "POLICY_APP_CV")
        return payload
    except _Stop:
        raise
    except Exception:
        raise _Stop("POLICY_APP_CV") from None
    finally:
        if fd is not None:
            os.close(fd)
        if dfd is not None:
            os.close(dfd)


class PolicyApplicationRuntime:
    def __init__(self, browser: PolicyFormBrowser, ledger: PolicySubmissionLedger, *,
                 clock=_utc_now, owner_wait_seconds: float = 6 * 3600):
        self.browser, self.ledger, self._clock = browser, ledger, clock
        self.owner_wait_seconds = owner_wait_seconds
        self._last: datetime | None = None

    def clock(self) -> datetime:
        """Never earlier than a time this runtime already used."""
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise _Stop("POLICY_APP_CLOCK")
        now = now.astimezone(timezone.utc)
        if self._last is not None and now < self._last:
            now = self._last
        self._last = now
        return now

    # ---- helpers ---------------------------------------------------------------------

    def _ids(self, m: ExecutionManifest) -> dict:
        return dict(opportunity_key=m.opportunity_key, requisition_id=m.requisition_id,
                    profile_version=m.candidate_profile_version, execution_id=m.execution_id)

    def _surface(self, m: ExecutionManifest) -> dict:
        snap = self.browser.inspect()
        _require(isinstance(snap, dict) and snap.get("url") == m.apply_url, "POLICY_APP_SURFACE_CHANGED")
        fields, upload = snap.get("fields"), snap.get("cv_upload")
        try:
            live = form_fingerprint(fields, upload)
        except Exception:
            raise _Stop("POLICY_APP_FORM_DRIFT") from None
        _require(live == m.form_fingerprint, "POLICY_APP_FORM_DRIFT")
        _require(type(snap.get("challenge")) is bool, "POLICY_APP_SURFACE_CHANGED")
        return snap

    @staticmethod
    def _filled(m: ExecutionManifest) -> tuple[ManifestField, ...]:
        return tuple(f for f in m.fields if f.answer.mode != "Blank Optional")

    @staticmethod
    def _attachment_ok(m: ExecutionManifest, snap: dict) -> bool:
        item = snap.get("cv_attachment")
        return (isinstance(item, dict) and item.get("sha256") == m.cv_sha256
                and type(item.get("size")) is int and item["size"] == m.cv_size_bytes
                and item.get("filename") == m.cv_filename and item.get("visible") is True)

    def _verify_readbacks(self, m: ExecutionManifest) -> None:
        for field in self._filled(m):
            value = self.browser.readback(field.key, field.control)
            _require(type(value) is type(field.answer.value) and value == field.answer.value,
                     "POLICY_APP_READBACK_MISMATCH")

    def _settle_challenge(self, m: ExecutionManifest, snap: dict) -> dict:
        """CAPTCHA/MFA/login: only the owner may solve it. Never automated."""
        if snap["challenge"] is False:
            return snap
        self.ledger.await_user(**self._ids(m), now=self.clock())
        self._owner_handoff = True
        solved = self.browser.wait_for_owner(self.owner_wait_seconds) is True
        _require(solved, "POLICY_APP_OWNER_CHALLENGE_UNRESOLVED")
        self.ledger.resume(**self._ids(m), now=self.clock())
        # The owner had the page: from now on this runtime never releases the key.
        snap = self._surface(m)
        _require(snap["challenge"] is False, "POLICY_APP_OWNER_CHALLENGE_UNRESOLVED")
        return snap

    # ---- run -------------------------------------------------------------------------

    def run(self, raw: bytes, *, expected_sha256: str, cv_path: Path, switch: RuntimeSwitch) -> RunResult:
        try:
            decision_time = self.clock()
        except _Stop as error:
            return RunResult("BLOCKED", error.code)
        try:
            decision = authorize_manifest(self.ledger, raw, now=decision_time, switch=switch,
                                          expected_sha256=expected_sha256)
        except Exception:
            return RunResult("BLOCKED", "POLICY_APP_AUTHORIZE")
        if not decision.dispatch_allowed:
            return RunResult(decision.decision.value)
        m = decision.manifest
        started = False
        self._owner_handoff = False
        try:
            cv = read_cv(cv_path, m)
            self.browser.open(m.apply_url)
            snap = self._settle_challenge(m, self._surface(m))
            for field in self._filled(m):
                self.browser.write(field.key, field.control, field.answer.value)
                value = self.browser.readback(field.key, field.control)
                _require(type(value) is type(field.answer.value) and value == field.answer.value,
                         "POLICY_APP_READBACK_MISMATCH")
            upload = self.browser.upload(m.cv_upload_key, cv, m.cv_filename)
            _require(isinstance(upload, UploadResult) and upload.payload_sha256 == m.cv_sha256
                     and upload.payload_size == m.cv_size_bytes and upload.filename == m.cv_filename
                     and upload.attachment_visible is True, "POLICY_APP_UPLOAD_UNVERIFIED")
            # Everything again on a fresh surface: catches reactive overwrites.
            snap = self._settle_challenge(m, self._surface(m))
            self._verify_readbacks(m)
            _require(self._attachment_ok(m, snap), "POLICY_APP_UPLOAD_UNVERIFIED")
            _require(snap.get("form_valid") is True and snap.get("submit_visible") is True,
                     "POLICY_APP_FORM_NOT_READY")
            inspected_at = self.clock()
            self.ledger.mark_started(**self._ids(m), now=inspected_at)
            started = True
            observation = self.browser.submit_once()
            _require(isinstance(observation, SubmitObservation)
                     and observation.success_acknowledged is True and observation.confirmation_visible is True
                     and observation.challenge_detected is False
                     and type(observation.acknowledgement_sha256) is str
                     and _HEX.fullmatch(observation.acknowledgement_sha256)
                     and isinstance(observation.observed_at, datetime)
                     and observation.observed_at.tzinfo is not None
                     and inspected_at <= observation.observed_at <= self.clock(),
                     "POLICY_APP_CONFIRMATION_UNVERIFIED")
            evidence = ConfirmationEvidence(
                submit_identity(m.opportunity_key, m.requisition_id, m.candidate_profile_version),
                ConfirmationType.ATS_ACKNOWLEDGEMENT, "evidence:sha256:" + observation.acknowledgement_sha256,
                observation.observed_at.astimezone(timezone.utc).isoformat(), True, m.execution_id,
                m.requisition_id)
            _require(self.ledger.confirm(**self._ids(m), evidence=evidence, now=self.clock()),
                     "POLICY_APP_CONFIRMATION_UNVERIFIED")
            return RunResult("EMPLOYER_CONFIRMED", submit_clicks=1, evidence_ref=evidence.evidence_ref)
        except BaseException as error:  # noqa: BLE001 - every exit must leave a consistent ledger
            code = error.code if isinstance(error, _Stop) and _CODE.fullmatch(error.code) else "POLICY_APP_BLOCKED"
            if started:
                result = RunResult("SUBMITTED_UNCONFIRMED", code, submit_clicks=-1)
            else:
                result = RunResult("BLOCKED", code)
                if not self._owner_handoff:
                    try:
                        self.ledger.release(**self._ids(m), now=self._last or decision_time)
                    except BaseException:  # noqa: BLE001
                        pass  # a plain reservation expires on its own; no click happened
                else:
                    # The owner had the page and may have submitted by hand: the key
                    # is held for the owner and never released or expired here.
                    try:
                        self.ledger.hold_for_owner(**self._ids(m), now=self._last or decision_time)
                    except BaseException:  # noqa: BLE001
                        result = RunResult("BLOCKED", "POLICY_APP_OWNER_HOLD_FAILED")
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            return result
