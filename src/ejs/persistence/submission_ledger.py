"""Private durable intent for the first single application; no network authority.

The consumer must pin one canonical ledger path. An intent consumes its sole slot
forever, including crashes, ambiguous outcomes and a changed target/profile. This
module intentionally exposes no delete, reset, retry or alternative-scope API.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat

from ejs.domain.submission import (
    ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus,
)


class SubmissionLedgerError(RuntimeError):
    """A fixed, non-sensitive fail-closed storage or validation error."""


_SCHEMA_VERSION = 1
_APPLICATION_ID = 0x454A5301
_EVIDENCE_REF = re.compile(r"evidence:sha256:[0-9a-f]{64}\Z")
_HASH_KEYS = (
    "submit_key", "execution_id", "opportunity_key", "requisition_id",
    "candidate_profile_version", "policy_version", "validation_key", "ats_family", "form_fingerprint",
)
_COLUMNS = (
    "slot", *(key + "_sha256" for key in _HASH_KEYS), "status", "requested_at", "reserved_at",
    "started_at", "confirmed_at", "confirmation_observed_at", "evidence_type", "evidence_ref",
)
_STATES = tuple(state.value for state in (
    SubmissionAttemptStatus.VALIDATED, SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED, SubmissionAttemptStatus.CONFIRMED,
))


def _opaque_hash(kind: str, value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,1023}", value):
        raise SubmissionLedgerError("LEDGER_IDENTITY_INVALID")
    return hashlib.sha256(value.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise SubmissionLedgerError("LEDGER_UTC_TIME_REQUIRED")
    return value.astimezone(timezone.utc)


def _parse_utc(value: str) -> datetime:
    if type(value) is not str or len(value) > 40:
        raise SubmissionLedgerError("LEDGER_UTC_TIME_REQUIRED")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (TypeError, ValueError, OverflowError):
        raise SubmissionLedgerError("LEDGER_UTC_TIME_REQUIRED") from None


def _time_text(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


class SqliteSubmissionLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        created = False
        try:
            parent = self.path.parent
            if not parent.exists():
                parent.mkdir(mode=0o700)
                parent_fd = os.open(parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            self._check_parent()
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            else:
                created = True
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._sync_parent()
            self._check_file()
            with self._transaction(initialize=created) as connection:
                if created:
                    definitions = ["slot INTEGER PRIMARY KEY CHECK(slot = 1)"]
                    definitions.extend(key + "_sha256 TEXT NOT NULL CHECK(length(" + key + "_sha256) = 64)" for key in _HASH_KEYS)
                    definitions.extend([
                        "status TEXT NOT NULL CHECK(status IN ('Validated','Submitted / Unconfirmed','Confirmed'))",
                        "requested_at TEXT NOT NULL", "reserved_at TEXT NOT NULL", "started_at TEXT",
                        "confirmed_at TEXT", "confirmation_observed_at TEXT", "evidence_type TEXT", "evidence_ref TEXT",
                    ])
                    connection.execute("CREATE TABLE first_application (" + ",".join(definitions) + ")")
                    connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                self._check_schema(connection)
        except SubmissionLedgerError:
            raise
        except (OSError, sqlite3.Error, ValueError):
            # Leave failed initialization present: an empty/corrupt existing file
            # must not be silently rebuilt and reopen the one-application budget.
            raise SubmissionLedgerError("LEDGER_OPEN_FAILED") from None

    def _check_parent(self) -> None:
        for component in (self.path.parent, *self.path.parent.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise SubmissionLedgerError("LEDGER_PATH_UNSAFE")
        info = self.path.parent.lstat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise SubmissionLedgerError("LEDGER_PARENT_NOT_PRIVATE")

    def _check_file(self) -> None:
        self._check_parent()
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise SubmissionLedgerError("LEDGER_FILE_UNSAFE")
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                metadata = sidecar.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_nlink != 1:
                    raise SubmissionLedgerError("LEDGER_FILE_UNSAFE")

    def _sync_parent(self) -> None:
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def _transaction(self, *, initialize: bool = False):
        connection = None
        try:
            self._check_file()
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise SubmissionLedgerError("LEDGER_JOURNAL_MODE_INVALID")
            connection.execute("BEGIN IMMEDIATE")
            if not initialize:
                self._check_schema(connection)
            yield connection
            self._check_file()
            connection.execute("COMMIT")
            self._sync_parent()
        except SubmissionLedgerError:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        except (OSError, sqlite3.Error, ValueError):
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise SubmissionLedgerError("LEDGER_STORAGE_FAILED") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    raise SubmissionLedgerError("LEDGER_STORAGE_FAILED") from None

    @staticmethod
    def _check_schema(connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (_SCHEMA_VERSION, 2) or connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID:
            raise SubmissionLedgerError("LEDGER_SCHEMA_INVALID")
        tables = connection.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        expected = [("first_application", "table")]
        if version == 2:
            expected += [("reviewed_scope_grants", "table"), ("reviewed_scope_attempts", "table")]
        if sorted((row[0], row[1]) for row in tables) != sorted(expected):
            raise SubmissionLedgerError("LEDGER_SCHEMA_INVALID")
        if tuple(row[1] for row in connection.execute("PRAGMA table_info(first_application)")) != _COLUMNS:
            raise SubmissionLedgerError("LEDGER_SCHEMA_INVALID")
        rows = connection.execute("SELECT * FROM first_application").fetchall()
        if len(rows) > 1:
            raise SubmissionLedgerError("LEDGER_STATE_INVALID")
        for row in rows:
            if row["slot"] != 1 or row["status"] not in _STATES or any(
                type(row[key + "_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", row[key + "_sha256"])
                for key in _HASH_KEYS
            ):
                raise SubmissionLedgerError("LEDGER_STATE_INVALID")
            requested = _parse_utc(row["requested_at"])
            reserved = _parse_utc(row["reserved_at"])
            if requested > reserved:
                raise SubmissionLedgerError("LEDGER_STATE_INVALID")
            confirmation_fields = ("confirmed_at", "confirmation_observed_at", "evidence_type", "evidence_ref")
            if row["status"] == SubmissionAttemptStatus.VALIDATED.value:
                if row["started_at"] is not None or any(row[key] is not None for key in confirmation_fields):
                    raise SubmissionLedgerError("LEDGER_STATE_INVALID")
            else:
                started = _parse_utc(row["started_at"])
                if started < reserved:
                    raise SubmissionLedgerError("LEDGER_STATE_INVALID")
                if row["status"] == SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value:
                    if any(row[key] is not None for key in confirmation_fields):
                        raise SubmissionLedgerError("LEDGER_STATE_INVALID")
                elif (
                    _parse_utc(row["confirmed_at"]) < _parse_utc(row["confirmation_observed_at"])
                    or _parse_utc(row["confirmation_observed_at"]) < started
                    or row["evidence_type"] not in {kind.value for kind in ConfirmationType}
                    or type(row["evidence_ref"]) is not str or not _EVIDENCE_REF.fullmatch(row["evidence_ref"])
                ):
                    raise SubmissionLedgerError("LEDGER_STATE_INVALID")
        if version == 2:
            # Additive reviewed extension only. The original slot, row schema,
            # state checks and all legacy methods remain unchanged/consumed.
            from ejs.persistence.reviewed_scope_ledger import check_reviewed_scope_extension
            check_reviewed_scope_extension(connection)

    def reserve_intent(self, attempt: SubmissionAttempt, now: datetime) -> bool:
        now = _utc(now)
        if not isinstance(attempt, SubmissionAttempt) or attempt.status is not SubmissionAttemptStatus.VALIDATED:
            raise SubmissionLedgerError("LEDGER_VALIDATED_ATTEMPT_REQUIRED")
        hashes = tuple(_opaque_hash(key, getattr(attempt, key)) for key in _HASH_KEYS)
        requested = _parse_utc(attempt.requested_at)
        if requested > now:
            raise SubmissionLedgerError("LEDGER_TIME_ORDER_INVALID")
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM first_application").fetchone():
                return False
            columns = ("slot", *(key + "_sha256" for key in _HASH_KEYS), "status", "requested_at", "reserved_at")
            values = (1, *hashes, SubmissionAttemptStatus.VALIDATED.value, _time_text(requested), _time_text(now))
            connection.execute("INSERT INTO first_application (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in values) + ")", values)
        return True

    def has_submit_key(self, key: str) -> bool:
        key_hash = _opaque_hash("submit_key", key)
        with self._transaction() as connection:
            return connection.execute("SELECT 1 FROM first_application WHERE submit_key_sha256=?", (key_hash,)).fetchone() is not None

    def has_intent(self) -> bool:
        with self._transaction() as connection:
            return connection.execute("SELECT 1 FROM first_application").fetchone() is not None

    def mark_started(self, submit_key: str, execution_id: str, started_at: datetime) -> None:
        started = _utc(started_at)
        key_hash = _opaque_hash("submit_key", submit_key)
        execution_hash = _opaque_hash("execution_id", execution_id)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM first_application WHERE submit_key_sha256=? AND execution_id_sha256=?", (key_hash, execution_hash)).fetchone()
            if row is None or row["status"] != SubmissionAttemptStatus.VALIDATED.value or started < _parse_utc(row["reserved_at"]):
                raise SubmissionLedgerError("LEDGER_START_REJECTED")
            connection.execute("UPDATE first_application SET status=?,started_at=? WHERE slot=1", (SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value, _time_text(started)))

    def confirm(self, submit_key: str, evidence: ConfirmationEvidence, now: datetime) -> bool:
        now = _utc(now)
        if not isinstance(evidence, ConfirmationEvidence) or evidence.exact_identity_match is not True or not isinstance(evidence.evidence_type, ConfirmationType) or type(evidence.evidence_ref) is not str or not _EVIDENCE_REF.fullmatch(evidence.evidence_ref):
            return False
        try:
            key_hash = _opaque_hash("submit_key", submit_key)
            evidence_hash = _opaque_hash("submit_key", evidence.submit_key)
            execution_hash = _opaque_hash("execution_id", evidence.execution_id)
            requisition_hash = _opaque_hash("requisition_id", evidence.requisition_id)
            observed = _parse_utc(evidence.observed_at)
        except SubmissionLedgerError:
            return False
        if key_hash != evidence_hash or observed > now:
            return False
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM first_application WHERE submit_key_sha256=? AND execution_id_sha256=? AND requisition_id_sha256=?", (key_hash, execution_hash, requisition_hash)).fetchone()
            if row is None or row["status"] not in (SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED.value, SubmissionAttemptStatus.CONFIRMED.value) or row["started_at"] is None or observed < _parse_utc(row["started_at"]):
                return False
            if row["status"] == SubmissionAttemptStatus.CONFIRMED.value:
                return row["evidence_ref"] == evidence.evidence_ref and row["evidence_type"] == evidence.evidence_type.value and row["confirmation_observed_at"] == _time_text(observed)
            connection.execute("UPDATE first_application SET status=?,confirmed_at=?,confirmation_observed_at=?,evidence_type=?,evidence_ref=? WHERE slot=1", (
                SubmissionAttemptStatus.CONFIRMED.value, _time_text(now), _time_text(observed), evidence.evidence_type.value, evidence.evidence_ref))
        return True

    def snapshot(self) -> dict | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM first_application").fetchone()
            if row is None:
                return None
            return {
                "status": row["status"], "submit_key_digest": row["submit_key_sha256"],
                "execution_digest": row["execution_id_sha256"], "requisition_digest": row["requisition_id_sha256"],
                "validation_digest": row["validation_key_sha256"], "requested_at": row["requested_at"],
                "started_at": row["started_at"], "confirmed_at": row["confirmed_at"],
                "evidence_digest": row["evidence_ref"].split(":")[-1] if row["evidence_ref"] else None,
            }
