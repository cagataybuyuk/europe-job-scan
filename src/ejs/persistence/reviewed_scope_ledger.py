"""One additive reviewed scope in the SAME canonical first-application database.

No reset, replacement database, third slot or caller-selected ledger path exists
in this API. The trusted bootstrap passes its already fixed legacy ledger. This
module performs only private durable state operations, never browser/network I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from threading import get_ident
from weakref import WeakKeyDictionary

from ejs.domain.submission import (
    ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus,
)
from ejs.persistence.submission_ledger import (
    SqliteSubmissionLedger, SubmissionLedgerError, _HASH_KEYS, _opaque_hash, _parse_utc,
    _time_text, _utc,
)
from ejs.services.reviewed_execution_scope import (
    EXPECTED_FUTURE_DECISION_REF, ReviewedExecutionScope, legacy_snapshot_fingerprint,
    require_issued_execution_scope,
)


_ERROR = "REVIEWED_SCOPE_LEDGER_REJECTED"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_EVIDENCE = re.compile(r"evidence:sha256:[a-f0-9]{64}\Z")
_ISSUED_VIEWS: WeakKeyDictionary = WeakKeyDictionary()
_GRANT_HASHES = (
    "scope_id_sha256", "grant_sha256", "decision_sha256", "source_sha256",
    "manifest_sha256", "facts_sha256", "cv_sha256", "candidate_profile_sha256",
    "execution_sha256", "posting_sha256", "publication_sha256",
    "company_requisition_sha256", "requisition_sha256", "legacy_snapshot_sha256",
)
_GRANT_COLUMNS = ("slot", *_GRANT_HASHES, "reviewed_at", "expires_at")
_ATTEMPT_HASHES = tuple(key + "_sha256" for key in _HASH_KEYS)
_ATTEMPT_TIMES = ("requested_at", "reserved_at", "started_at", "confirmed_at", "confirmation_observed_at")
_ATTEMPT_COLUMNS = ("slot", "owner_sha256", "status", "preparing_at", *_ATTEMPT_HASHES,
                    *_ATTEMPT_TIMES, "evidence_type", "evidence_ref")
_GRANT_SQL = ("CREATE TABLE reviewed_scope_grants (slot INTEGER PRIMARY KEY CHECK(slot = 2),"
    + ",".join(name + " TEXT NOT NULL CHECK(length(" + name + ") = 64)" for name in _GRANT_HASHES)
    + ",reviewed_at TEXT NOT NULL,expires_at TEXT NOT NULL)")
_ATTEMPT_SQL = ("CREATE TABLE reviewed_scope_attempts (slot INTEGER PRIMARY KEY CHECK(slot = 2),"
    "owner_sha256 TEXT NOT NULL CHECK(length(owner_sha256) = 64),"
    "status TEXT NOT NULL CHECK(status IN ('Preparing','Validated','Submitted / Unconfirmed','Confirmed')),"
    "preparing_at TEXT NOT NULL," + ",".join(name + " TEXT" for name in _ATTEMPT_HASHES)
    + "," + ",".join(name + " TEXT" for name in _ATTEMPT_TIMES)
    + ",evidence_type TEXT,evidence_ref TEXT)")


def _require(condition: bool) -> None:
    if not condition:
        raise SubmissionLedgerError(_ERROR)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hex(value) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _view_seal(view) -> tuple:
    return (id(view._legacy), str(view._legacy.path), id(view._scope), view._owner,
            os.getpid(), get_ident())


def require_owned_scope_view(view) -> None:
    """Require the exact issued view on its owning process and browser thread."""
    try:
        _require(type(view) is ReviewedScopeLedger
                 and _ISSUED_VIEWS.get(view) == _view_seal(view))
        require_issued_execution_scope(view._scope, require_current=False)
    except Exception:
        raise SubmissionLedgerError(_ERROR) from None


def _legacy_snapshot(connection) -> dict:
    row = connection.execute("SELECT * FROM first_application").fetchone()
    _require(row is not None)
    return {"status": row["status"], "submit_key_digest": row["submit_key_sha256"],
        "execution_digest": row["execution_id_sha256"], "requisition_digest": row["requisition_id_sha256"],
        "validation_digest": row["validation_key_sha256"], "requested_at": row["requested_at"],
        "started_at": row["started_at"], "confirmed_at": row["confirmed_at"],
        "evidence_digest": row["evidence_ref"].split(":")[-1] if row["evidence_ref"] else None}


def _grant_values(scope: ReviewedExecutionScope) -> tuple:
    target = scope.target
    return (2, _hash(scope.scope_id), scope.grant_sha256, _hash(scope.user_decision_ref),
        _hash(scope.source_sha), scope.manifest_sha256, scope.facts_sha256, scope.cv_sha256,
        _hash(scope.candidate_profile_version), _hash(scope.execution_id), _hash(target.posting_id),
        _hash(target.publication_id), _hash(target.company_id + ":" + target.requisition_id.casefold()),
        _hash(target.requisition_id), scope.legacy_snapshot_sha256,
        _time_text(scope.reviewed_at), _time_text(scope.expires_at))


def check_reviewed_scope_extension(connection) -> None:
    """Strict v2 schema/data validation used by the unchanged legacy view too."""
    try:
        for table, columns, sql in (
            ("reviewed_scope_grants", _GRANT_COLUMNS, _GRANT_SQL),
            ("reviewed_scope_attempts", _ATTEMPT_COLUMNS, _ATTEMPT_SQL),
        ):
            _require(tuple(row[1] for row in connection.execute("PRAGMA table_info(" + table + ")")) == columns)
            definition = connection.execute("SELECT sql FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone()
            _require(definition is not None and definition[0] == sql)
        grants = connection.execute("SELECT * FROM reviewed_scope_grants").fetchall()
        _require(len(grants) == 1)
        grant = grants[0]
        _require(grant["slot"] == 2 and all(_hex(grant[key]) for key in _GRANT_HASHES))
        _require(grant["decision_sha256"] == _hash(EXPECTED_FUTURE_DECISION_REF)
                 and grant["posting_sha256"] == _hash("744000156831399")
                 and grant["publication_sha256"] == _hash("3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e")
                 and grant["requisition_sha256"] == _hash("REF00063")
                 and grant["legacy_snapshot_sha256"] == legacy_snapshot_fingerprint(_legacy_snapshot(connection)))
        reviewed, expires = _parse_utc(grant["reviewed_at"]), _parse_utc(grant["expires_at"])
        _require(timedelta(0) < expires - reviewed <= timedelta(hours=24))
        rows = connection.execute("SELECT * FROM reviewed_scope_attempts").fetchall()
        _require(len(rows) <= 1)
        if not rows:
            return
        row = rows[0]
        _require(row["slot"] == 2 and _hex(row["owner_sha256"])
                 and row["status"] in {"Preparing", "Validated", "Submitted / Unconfirmed", "Confirmed"})
        preparing = _parse_utc(row["preparing_at"])
        _require(reviewed <= preparing < expires)
        rest = (*_ATTEMPT_HASHES, *_ATTEMPT_TIMES, "evidence_type", "evidence_ref")
        if row["status"] == "Preparing":
            _require(all(row[key] is None for key in rest))
            return
        _require(all(_hex(row[key]) for key in _ATTEMPT_HASHES)
                 and row["execution_id_sha256"] == grant["execution_sha256"]
                 and row["candidate_profile_version_sha256"] == grant["candidate_profile_sha256"]
                 and row["requisition_id_sha256"] == grant["requisition_sha256"])
        reserved, requested = _parse_utc(row["reserved_at"]), _parse_utc(row["requested_at"])
        _require(preparing <= requested <= reserved < expires)
        confirmation = ("confirmed_at", "confirmation_observed_at", "evidence_type", "evidence_ref")
        if row["status"] == "Validated":
            _require(row["started_at"] is None and all(row[key] is None for key in confirmation))
            return
        started = _parse_utc(row["started_at"])
        _require(reserved <= started < expires)
        if row["status"] == "Submitted / Unconfirmed":
            _require(all(row[key] is None for key in confirmation))
            return
        _require(started <= _parse_utc(row["confirmation_observed_at"]) <= _parse_utc(row["confirmed_at"])
                 and row["evidence_type"] in {kind.value for kind in ConfirmationType}
                 and type(row["evidence_ref"]) is str and _EVIDENCE.fullmatch(row["evidence_ref"]) is not None)
    except Exception:
        raise SubmissionLedgerError(_ERROR) from None


def _bound_grant(connection, scope: ReviewedExecutionScope) -> None:
    values = connection.execute("SELECT * FROM reviewed_scope_grants WHERE slot=2").fetchone()
    _require(values is not None and tuple(values) == _grant_values(scope))
    _require(scope.legacy_snapshot_sha256 == legacy_snapshot_fingerprint(_legacy_snapshot(connection)))


def register_reviewed_execution_scope(legacy: SqliteSubmissionLedger, scope: ReviewedExecutionScope,
                                      *, now: datetime) -> bool:
    """Explicit atomic additive v1→v2 migration; returns False for exact replay.

    Caller supplies the already canonical ledger, never a new path. This function
    requires a currently issued exact-target operation grant. It never changes
    first_application. A different grant/profile/scope cannot replace slot two.
    """
    try:
        require_issued_execution_scope(scope, now=now)
        _require(type(legacy) is SqliteSubmissionLedger)
        with legacy._transaction() as connection:
            _require(scope.legacy_snapshot_sha256 == legacy_snapshot_fingerprint(_legacy_snapshot(connection)))
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                _bound_grant(connection, scope)
                return False
            _require(version == 1)
            # The prior target is already consumed. No changed alias/profile is
            # an escape: this module only accepts the distinct fixed next target.
            _require(scope.target.posting_id != "744000159638089"
                     and scope.target.publication_id != "aaf6587c-8336-4454-adb1-e09661d9bdfd"
                     and not (scope.target.company_id == "593fe2a3e4b0ac9d00fb37f8"
                              and scope.target.requisition_id.casefold() == "ref7089x"))
            connection.execute(_GRANT_SQL)
            connection.execute(_ATTEMPT_SQL)
            connection.execute("INSERT INTO reviewed_scope_grants (" + ",".join(_GRANT_COLUMNS)
                               + ") VALUES (" + ",".join("?" for _ in _GRANT_COLUMNS) + ")", _grant_values(scope))
            connection.execute("PRAGMA user_version=2")
            legacy._check_schema(connection)
        return True
    except Exception:
        raise SubmissionLedgerError(_ERROR) from None


@dataclass(frozen=True, init=False, eq=False)
class ReviewedScopeLedger:
    """Memory-owned scope view returned ONLY after a durable preparation claim."""

    _legacy: SqliteSubmissionLedger = field(repr=False)
    _scope: ReviewedExecutionScope = field(repr=False)
    _owner: bytes = field(repr=False)

    @property
    def path(self):
        require_owned_scope_view(self)
        return self._legacy.path

    def _row(self, connection, *, own: bool = True):
        require_owned_scope_view(self)
        require_issued_execution_scope(self._scope, require_current=False)
        _bound_grant(connection, self._scope)
        row = connection.execute("SELECT * FROM reviewed_scope_attempts WHERE slot=2").fetchone()
        _require(row is not None)
        if own:
            _require(type(self._owner) is bytes and len(self._owner) == 32
                     and hmac.compare_digest(row["owner_sha256"], hashlib.sha256(self._owner).hexdigest()))
        return row

    def has_intent(self) -> bool:
        try:
            require_owned_scope_view(self)
            require_issued_execution_scope(self._scope, now=_now())
            with self._legacy._transaction() as connection:
                row = self._row(connection)
                return row["status"] != "Preparing"
        except Exception:
            raise SubmissionLedgerError(_ERROR) from None

    def has_submit_key(self, key: str) -> bool:
        try:
            require_owned_scope_view(self)
            digest = _opaque_hash("submit_key", key)
            with self._legacy._transaction() as connection:
                self._row(connection)
                return (connection.execute("SELECT 1 FROM first_application WHERE submit_key_sha256=?", (digest,)).fetchone() is not None
                        or connection.execute("SELECT 1 FROM reviewed_scope_attempts WHERE submit_key_sha256=?", (digest,)).fetchone() is not None)
        except Exception:
            raise SubmissionLedgerError(_ERROR) from None

    def reserve_intent(self, attempt: SubmissionAttempt, now: datetime) -> bool:
        try:
            require_owned_scope_view(self)
            now = _utc(now)
            require_issued_execution_scope(self._scope, now=now)
            _require(type(attempt) is SubmissionAttempt and attempt.status is SubmissionAttemptStatus.VALIDATED
                     and attempt.execution_id == self._scope.execution_id
                     and attempt.requisition_id == self._scope.target.requisition_id
                     and attempt.candidate_profile_version == self._scope.candidate_profile_version)
            hashes = tuple(_opaque_hash(key, getattr(attempt, key)) for key in _HASH_KEYS)
            requested = _parse_utc(attempt.requested_at)
            with self._legacy._transaction() as connection:
                row = self._row(connection)
                if row["status"] != "Preparing":
                    return False
                _require(_parse_utc(row["preparing_at"]) <= requested <= now)
                # Check all prior submit identities as well as fixed target
                # aliases, independent of a new candidate profile version.
                _require(connection.execute("SELECT 1 FROM first_application WHERE submit_key_sha256=?", (hashes[0],)).fetchone() is None)
                updates = [name + "=?" for name in _ATTEMPT_HASHES]
                connection.execute("UPDATE reviewed_scope_attempts SET " + ",".join(updates)
                    + ",status='Validated',requested_at=?,reserved_at=? WHERE slot=2",
                    (*hashes, _time_text(requested), _time_text(now)))
                check_reviewed_scope_extension(connection)
            return True
        except Exception:
            raise SubmissionLedgerError(_ERROR) from None

    def mark_started(self, submit_key: str, execution_id: str, started_at: datetime) -> None:
        try:
            require_owned_scope_view(self)
            started = _utc(started_at)
            require_issued_execution_scope(self._scope, now=started)
            key = _opaque_hash("submit_key", submit_key)
            execution = _opaque_hash("execution_id", execution_id)
            with self._legacy._transaction() as connection:
                row = self._row(connection)
                _require(row["status"] == "Validated" and row["submit_key_sha256"] == key
                         and row["execution_id_sha256"] == execution
                         and _parse_utc(row["reserved_at"]) <= started)
                connection.execute("UPDATE reviewed_scope_attempts SET status='Submitted / Unconfirmed',started_at=? WHERE slot=2", (_time_text(started),))
                check_reviewed_scope_extension(connection)
        except Exception:
            raise SubmissionLedgerError(_ERROR) from None

    def confirm(self, submit_key: str, evidence: ConfirmationEvidence, now: datetime) -> bool:
        try:
            require_owned_scope_view(self)
            now = _utc(now)
            # Late authentic confirmation is still recorded after the grant
            # expires; this can never reopen preparation or dispatch.
            require_issued_execution_scope(self._scope, require_current=False)
            if (type(evidence) is not ConfirmationEvidence or evidence.exact_identity_match is not True
                or not isinstance(evidence.evidence_type, ConfirmationType)
                or type(evidence.evidence_ref) is not str or _EVIDENCE.fullmatch(evidence.evidence_ref) is None):
                return False
            key = _opaque_hash("submit_key", submit_key)
            if key != _opaque_hash("submit_key", evidence.submit_key):
                return False
            execution = _opaque_hash("execution_id", evidence.execution_id)
            requisition = _opaque_hash("requisition_id", evidence.requisition_id)
            observed = _parse_utc(evidence.observed_at)
            with self._legacy._transaction() as connection:
                row = self._row(connection)
                if (row["status"] not in {"Submitted / Unconfirmed", "Confirmed"}
                    or row["submit_key_sha256"] != key or row["execution_id_sha256"] != execution
                    or row["requisition_id_sha256"] != requisition
                    or not _parse_utc(row["started_at"]) <= observed <= now):
                    return False
                if row["status"] == "Confirmed":
                    return (row["evidence_ref"] == evidence.evidence_ref
                            and row["evidence_type"] == evidence.evidence_type.value
                            and row["confirmation_observed_at"] == _time_text(observed))
                connection.execute("UPDATE reviewed_scope_attempts SET status='Confirmed',confirmed_at=?,confirmation_observed_at=?,evidence_type=?,evidence_ref=? WHERE slot=2",
                    (_time_text(now), _time_text(observed), evidence.evidence_type.value, evidence.evidence_ref))
                check_reviewed_scope_extension(connection)
            return True
        except Exception:
            raise SubmissionLedgerError(_ERROR) from None

    def snapshot(self) -> dict | None:
        try:
            require_owned_scope_view(self)
            with self._legacy._transaction() as connection:
                row = self._row(connection)
                if row["status"] == "Preparing":
                    return None
                return {"status": row["status"], "submit_key_digest": row["submit_key_sha256"],
                    "execution_digest": row["execution_id_sha256"], "requisition_digest": row["requisition_id_sha256"],
                    "validation_digest": row["validation_key_sha256"], "requested_at": row["requested_at"],
                    "started_at": row["started_at"], "confirmed_at": row["confirmed_at"],
                    "evidence_digest": row["evidence_ref"].split(":")[-1] if row["evidence_ref"] else None}
        except Exception:
            raise SubmissionLedgerError(_ERROR) from None


def claim_reviewed_preparation(legacy: SqliteSubmissionLedger, scope: ReviewedExecutionScope,
                                *, now: datetime) -> ReviewedScopeLedger | None:
    """Claim before browser preparation; a failed/crashed claim is never released.

    This returns an in-memory owner only on the first successful transaction.
    Replayed grants, fresh processes and new source/profile/scope identities do
    not reconstruct its random token or regain the consumed preparation slot.
    """
    try:
        now = _utc(now)
        require_issued_execution_scope(scope, now=now)
        _require(type(legacy) is SqliteSubmissionLedger)
        owner = secrets.token_bytes(32)
        with legacy._transaction() as connection:
            _bound_grant(connection, scope)
            if connection.execute("SELECT 1 FROM reviewed_scope_attempts").fetchone():
                return None
            connection.execute("INSERT INTO reviewed_scope_attempts (slot,owner_sha256,status,preparing_at) VALUES (2,?,'Preparing',?)",
                               (hashlib.sha256(owner).hexdigest(), _time_text(now)))
            check_reviewed_scope_extension(connection)
        result = object.__new__(ReviewedScopeLedger)
        object.__setattr__(result, "_legacy", legacy)
        object.__setattr__(result, "_scope", scope)
        object.__setattr__(result, "_owner", owner)
        _ISSUED_VIEWS[result] = _view_seal(result)
        return result
    except Exception:
        raise SubmissionLedgerError(_ERROR) from None
