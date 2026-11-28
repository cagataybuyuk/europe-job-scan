"""W36-02 S3: standing-envelope admission at the engine's mandatory guard points.

The engine admits every claimed manifest before it creates a browser, then pins
the admitted envelope id, generation and record hashes for that run. Every later
guard point (and so the network boundary, which calls the same check) re-reads
the trusted root sources and the local execution package and admits again. A
missing, revoked, expired, unreadable or re-published envelope denies the next
effect; a newer generation never lets the pinned run continue.

The local package lives at ``<engine root>/packages/<execution id, ':' -> '~'>/``
under the owner-only file policy. The vacancy country is taken from the pinned
attestation and the matching rule is this code's own, never from the package's
claims. Paths, the trusted owner and the approval root are fixed in code: no
caller, user-id or process-variable override exists, and no un-admitted mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from ejs.services.current_fact_approval import MAX_REGISTRY_BYTES
from ejs.services.execution_manifest import MAX_CV_BYTES, MAX_MANIFEST_BYTES
from ejs.services.inventory_eligibility import MATCHING_RULE
from ejs.services.private_source_files import _read_private_sources
from ejs.services.reviewed_manifest_compiler import _MAX_SNAPSHOT_BYTES
from ejs.services.standing_admission import AdmissionEvidence, admit_standing_batch
from ejs.services.standing_envelope import BatchBinding, BatchInputs, read_trusted_batch_sources

ERROR = "STANDING_GUARD_DENIED"
PACKAGES = "packages"
_PACKAGE_FILES = (
    ("policy", "policy.json", MAX_MANIFEST_BYTES),
    ("profile", "profile.json", MAX_MANIFEST_BYTES),
    ("template", "template.json", MAX_MANIFEST_BYTES),
    ("registry", "registry.json", MAX_REGISTRY_BYTES),
    ("inventory", "inventory.json", MAX_REGISTRY_BYTES),
    ("eligibility", "eligibility.json", MAX_REGISTRY_BYTES),
    ("snapshot", "snapshot.json", _MAX_SNAPSHOT_BYTES),
    ("handoff", "handoff.json", MAX_REGISTRY_BYTES),
    ("country_attestation", "country-attestation.json", MAX_REGISTRY_BYTES),
)


def _require(condition: object) -> None:
    if not condition:
        raise PermissionError(ERROR)


def package_name(execution_id: str) -> str:
    """One-to-one, as for result files: '~' never occurs in an execution id."""
    return execution_id.replace(":", "~")


def _read_package(root: Path, cv_dir: Path, raw: bytes) -> tuple[BatchInputs, AdmissionEvidence]:
    document = json.loads(raw)  # ids only; admission validates the whole manifest
    execution_id, cv_name = document["execution_id"], document["cv"]["filename"]
    _require(type(execution_id) is str and type(cv_name) is str and "/" not in cv_name
             and "/" not in execution_id and cv_name not in ("", ".", ".."))
    folder = root / PACKAGES / package_name(execution_id)
    paths = tuple((folder / name, limit) for _, name, limit in _PACKAGE_FILES)
    *values, cv = _read_private_sources((*paths, (cv_dir / cv_name, MAX_CV_BYTES)))
    files = dict(zip((key for key, _, _ in _PACKAGE_FILES), values))
    # The attestation is pinned by the package and checked against the envelope.
    country = json.loads(files["country_attestation"])["country"]
    inputs = BatchInputs(manifest=raw, policy=files["policy"], profile=files["profile"],
                         template=files["template"], cv=cv, registry=files["registry"],
                         inventory=files["inventory"], eligibility=files["eligibility"],
                         country=country, matching_rule_version=MATCHING_RULE)
    return inputs, AdmissionEvidence(files["snapshot"], files["handoff"],
                                     files["country_attestation"])


@dataclass(frozen=True, repr=False)
class _Pin:
    binding: BatchBinding
    execution_id: str
    manifest_sha256: str


class StandingRun:
    """Admission for one claimed manifest; construct before any browser exists.

    ``check(point)`` raises unless a fresh admission from freshly read sources
    still yields exactly the binding pinned at construction. The clock is
    floored at the latest time this run used, so a clock that steps back
    never revives an expired envelope.
    """

    def __init__(self, root: Path, cv_dir: Path, raw: bytes, clock: Callable[[], datetime]):
        self._root, self._cv_dir, self._raw, self._clock = root, cv_dir, raw, clock
        self._floor: datetime | None = None
        try:
            _require(isinstance(root, Path) and isinstance(cv_dir, Path) and type(raw) is bytes
                     and callable(clock))
            decision = self._admit()
            self._pin = _Pin(decision.binding, decision.execution_id, decision.manifest_sha256)
            _require(self._pin.manifest_sha256 == hashlib.sha256(raw).hexdigest())
        except Exception:
            raise PermissionError(ERROR) from None

    @property
    def binding(self) -> BatchBinding:
        return self._pin.binding

    def _now(self) -> datetime:
        now = self._clock()
        _require(isinstance(now, datetime) and now.utcoffset() is not None)
        now = now.astimezone(timezone.utc)
        if self._floor is not None and now < self._floor:
            now = self._floor
        self._floor = now
        return now

    def _admit(self):
        sources = read_trusted_batch_sources()
        inputs, evidence = _read_package(self._root, self._cv_dir, self._raw)
        return admit_standing_batch(sources, inputs, evidence, now=self._now())

    def check(self, point: str) -> None:
        try:
            decision = self._admit()
            _require(decision.binding == self._pin.binding
                     and decision.execution_id == self._pin.execution_id
                     and decision.manifest_sha256 == self._pin.manifest_sha256)
            return
        except Exception:
            pass
        raise PermissionError(ERROR) from None
