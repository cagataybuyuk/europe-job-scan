"""GD-006 step 3: policy-based submissions in the SAME canonical ledger (v3).

Additive extension only. ``first_application`` (slot 1, the confirmed first
canary) and the optional reviewed-scope tables (slot 2) are never changed; their
submit keys and opportunity/requisition pairs stay consumed and block duplicates.

One row per normalized submit key. Lifecycle::

    Reserved ──► Submitted / Unconfirmed ──► Confirmed
       │  ▲
       ▼  │ (user solves CAPTCHA/MFA)
    Awaiting User
       │
       ▼
    Released   (only before dispatch; the key may be reserved again later)

``mark_started`` commits *before* the engine clicks submit. A started row is
never released, retried or replaced, even if the outcome stays ambiguous. A row
that was reserved but never started had no click, so a stale reservation is
released automatically. This module performs no browser or network I/O.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import re
from zoneinfo import ZoneInfo

from ejs.contracts.submit import _key_part, submit_identity
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType
from ejs.persistence.submission_ledger import (
    SqliteSubmissionLedger, SubmissionLedgerError, _parse_utc, _time_text, _utc,
)

SCHEMA_VERSION = 3
_ERROR = "POLICY_LEDGER_REJECTED"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_EVIDENCE = re.compile(r"evidence:sha256:[a-f0-9]{64}\Z")
_STAGE = re.compile(r"R[2-4]\Z")
_EXECUTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,199}\Z")
_ISTANBUL = ZoneInfo("Europe/Istanbul")

RESERVED, AWAITING, RELEASED = "Reserved", "Awaiting User", "Released"
STARTED, CONFIRMED = "Submitted / Unconfirmed", "Confirmed"
_STATES = (RESERVED, AWAITING, RELEASED, STARTED, CONFIRMED)
RESERVATION_TTL = timedelta(minutes=30)   # engine fills and clicks within this
AWAITING_TTL = timedelta(hours=12)        # user solves CAPTCHA/MFA within this

_HASHES = ("submit_key_sha256", "opportunity_requisition_sha256", "execution_id_sha256",
           "requisition_id_sha256", "manifest_sha256")
_TIMES = ("reserved_at", "awaiting_at", "released_at", "started_at", "confirmed_at",
          "confirmation_observed_at")
COLUMNS = (*_HASHES, "status", "rollout_stage", "daily_cap", *_TIMES, "started_day",
           "evidence_type", "evidence_ref")
TABLE_SQL = ("CREATE TABLE policy_submissions ("
             "submit_key_sha256 TEXT PRIMARY KEY CHECK(length(submit_key_sha256) = 64),"
             + ",".join(name + " TEXT NOT NULL CHECK(length(" + name + ") = 64)" for name in _HASHES[1:])
             + ",status TEXT NOT NULL CHECK(status IN ('Reserved','Awaiting User','Released',"
               "'Submitted / Unconfirmed','Confirmed')),rollout_stage TEXT NOT NULL,"
               "daily_cap INTEGER CHECK(daily_cap IS NULL OR daily_cap >= 1),"
             + ",".join(name + " TEXT" for name in _TIMES)
             + ",started_day TEXT,evidence_type TEXT,evidence_ref TEXT)")


def _require(condition: object) -> None:
    if not condition:
        raise SubmissionLedgerError(_ERROR)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _local_day(moment: datetime) -> str:
    return moment.astimezone(_ISTANBUL).date().isoformat()


def _identity(opportunity_key: str, requisition_id: str, profile_version: str) -> tuple[str, str]:
    """(submit key hash, opportunity+requisition hash) in the legacy hash form."""
    _require(all(type(v) is str and v.strip() for v in (opportunity_key, requisition_id, profile_version)))
    key = submit_identity(opportunity_key, requisition_id, profile_version)
    return _hash(key), _hash(_key_part(opportunity_key) + "\x1f" + _key_part(requisition_id))


def check_policy_extension(connection) -> None:
    """Strict v3 schema/data validation, called from the legacy schema check."""
    try:
        columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(policy_submissions)"))
        _require(columns == COLUMNS)
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='policy_submissions' AND type='table'").fetchone()
        _require(definition is not None and definition[0] == TABLE_SQL)
        for row in connection.execute("SELECT * FROM policy_submissions"):
            _require(all(type(row[k]) is str and _HEX.fullmatch(row[k]) for k in _HASHES))
            _require(row["status"] in _STATES and type(row["rollout_stage"]) is str
                     and _STAGE.fullmatch(row["rollout_stage"])
                     and (row["daily_cap"] is None or (type(row["daily_cap"]) is int and row["daily_cap"] >= 1)))
            reserved = _parse_utc(row["reserved_at"])
            status = row["status"]
            confirmation = ("confirmed_at", "confirmation_observed_at", "evidence_type", "evidence_ref")
            if status in (RESERVED, AWAITING, RELEASED):
                _require(row["started_at"] is None and row["started_day"] is None
                         and all(row[k] is None for k in confirmation))
                _require((row["awaiting_at"] is not None) == (status == AWAITING)
                         or (status == RELEASED))
                _require((row["released_at"] is not None) == (status == RELEASED))
                for name in ("awaiting_at", "released_at"):
                    if row[name] is not None:
                        _require(_parse_utc(row[name]) >= reserved)
                continue
            started = _parse_utc(row["started_at"])
            _require(started >= reserved and row["released_at"] is None
                     and row["started_day"] == _local_day(started))
            if status == STARTED:
                _require(all(row[k] is None for k in confirmation))
                continue
            _require(started <= _parse_utc(row["confirmation_observed_at"]) <= _parse_utc(row["confirmed_at"])
                     and row["evidence_type"] in {kind.value for kind in ConfirmationType}
                     and type(row["evidence_ref"]) is str and _EVIDENCE.fullmatch(row["evidence_ref"]))
    except SubmissionLedgerError:
        raise
    except Exception:
        raise SubmissionLedgerError(_ERROR) from None


def enable_policy_submissions(legacy: SqliteSubmissionLedger, *, now: datetime) -> "PolicySubmissionLedger":
    """Atomic additive v1/v2 -> v3 migration; an existing v3 ledger is reused as is.

    The caller passes the already pinned canonical ledger, never a new path. A v2
    ledger migrates only once its reviewed attempt can no longer submit: it was
    dispatched, or its grant has expired without a reservation.
    """
    _require(type(legacy) is SqliteSubmissionLedger)
    now = _utc(now)
    with legacy._transaction() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            _require(version in (1, 2))
            if version == 2:
                grant = connection.execute("SELECT expires_at FROM reviewed_scope_grants").fetchone()
                attempt = connection.execute("SELECT status FROM reviewed_scope_attempts").fetchone()
                finished = attempt is not None and attempt["status"] in (STARTED, CONFIRMED)
                _require(finished or (now >= _parse_utc(grant["expires_at"])
                                      and (attempt is None or attempt["status"] in ("Preparing", "Validated"))))
            connection.execute(TABLE_SQL)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            legacy._check_schema(connection)
    return PolicySubmissionLedger(legacy)


class PolicySubmissionLedger:
    def __init__(self, legacy: SqliteSubmissionLedger):
        _require(type(legacy) is SqliteSubmissionLedger)
        with legacy._transaction() as connection:
            _require(connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION)
        self._legacy = legacy

    # ---- reads -----------------------------------------------------------------

    @staticmethod
    def _consumed_elsewhere(connection, key_hash: str, requisition_id: str) -> bool:
        """True when slot 1 or the reviewed slot already used this key or requisition.

        Older tables store hashes of the raw spelling, so the requisition is
        compared under its common spellings and alone (fail closed: a rare
        cross-employer requisition collision blocks rather than duplicates).
        """
        raw = requisition_id.strip()
        bare = re.sub(r"[^A-Za-z0-9]", "", raw)
        spellings = {raw, raw.upper(), raw.lower(), raw.casefold(), bare, bare.upper(), bare.lower()}
        req_hashes = [_hash(item) for item in spellings]
        marks = ",".join("?" for _ in req_hashes)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("first_application", "reviewed_scope_attempts"):
            if table in tables and connection.execute(
                    f"SELECT 1 FROM {table} WHERE submit_key_sha256=? OR requisition_id_sha256 IN ({marks})",
                    (key_hash, *req_hashes)).fetchone():
                return True
        # The reviewed grant pins its target even before an attempt row has hashes.
        return "reviewed_scope_grants" in tables and connection.execute(
            f"SELECT 1 FROM reviewed_scope_grants WHERE requisition_sha256 IN ({marks})",
            tuple(req_hashes)).fetchone() is not None

    def started_today(self, now: datetime) -> int:
        """Dispatched submissions on today's Istanbul calendar day (cap basis)."""
        with self._legacy._transaction() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM policy_submissions WHERE started_day=?",
                (_local_day(_utc(now)),)).fetchone()[0]

    def status(self, opportunity_key: str, requisition_id: str, profile_version: str) -> str | None:
        key_hash, _ = _identity(opportunity_key, requisition_id, profile_version)
        with self._legacy._transaction() as connection:
            row = connection.execute("SELECT status FROM policy_submissions WHERE submit_key_sha256=?",
                                     (key_hash,)).fetchone()
            return None if row is None else row["status"]

    # ---- transitions -------------------------------------------------------------

    def reserve(self, *, opportunity_key: str, requisition_id: str, profile_version: str,
                execution_id: str, manifest_sha256: str, rollout_stage: str, daily_cap: int | None,
                now: datetime) -> str:
        """Claim one submission. Returns RESERVED, DUPLICATE, CAP_REACHED or BUSY."""
        now = _utc(now)
        _require(type(manifest_sha256) is str and _HEX.fullmatch(manifest_sha256))
        _require(type(rollout_stage) is str and _STAGE.fullmatch(rollout_stage))
        _require(daily_cap is None or (type(daily_cap) is int and daily_cap >= 1))
        _require(type(execution_id) is str and _EXECUTION.fullmatch(execution_id))
        key_hash, pair_hash = _identity(opportunity_key, requisition_id, profile_version)
        with self._legacy._transaction() as connection:
            # A reservation that was never started had no click: expire it.
            for row in connection.execute(
                    "SELECT submit_key_sha256,status,reserved_at,awaiting_at FROM policy_submissions "
                    "WHERE status IN (?,?)", (RESERVED, AWAITING)).fetchall():
                since = _parse_utc(row["awaiting_at"] or row["reserved_at"])
                ttl = AWAITING_TTL if row["status"] == AWAITING else RESERVATION_TTL
                if now - since > ttl:
                    connection.execute("UPDATE policy_submissions SET status=?,released_at=? "
                                       "WHERE submit_key_sha256=?",
                                       (RELEASED, _time_text(now), row["submit_key_sha256"]))
            if self._consumed_elsewhere(connection, key_hash, requisition_id):
                return "DUPLICATE"
            blocking = connection.execute(
                "SELECT 1 FROM policy_submissions WHERE (submit_key_sha256=? OR "
                "opportunity_requisition_sha256=?) AND status!=?",
                (key_hash, pair_hash, RELEASED)).fetchone()
            if blocking:
                return "DUPLICATE"
            if connection.execute("SELECT 1 FROM policy_submissions WHERE status=?",
                                  (RESERVED,)).fetchone():
                return "BUSY"  # G7: one engine dispatch in flight at a time
            if daily_cap is not None and self._committed_today(connection, now) >= daily_cap:
                return "CAP_REACHED"
            values = (key_hash, pair_hash, _hash(execution_id), _hash(requisition_id.strip()),
                      manifest_sha256, RESERVED, rollout_stage, daily_cap, _time_text(now))
            connection.execute("DELETE FROM policy_submissions WHERE submit_key_sha256=? AND status=?",
                               (key_hash, RELEASED))
            connection.execute(
                "INSERT INTO policy_submissions (" + ",".join(COLUMNS[:9]) + ") VALUES ("
                + ",".join("?" for _ in values) + ")", values)
        return "RESERVED"

    @staticmethod
    def _committed_today(connection, now: datetime) -> int:
        """Started today plus every claim that may still start (cap basis)."""
        return connection.execute(
            "SELECT COUNT(*) FROM policy_submissions WHERE started_day=? OR status IN (?,?)",
            (_local_day(now), RESERVED, AWAITING)).fetchone()[0]

    def _row(self, connection, submit_key_hash: str, execution_id: str):
        row = connection.execute("SELECT * FROM policy_submissions WHERE submit_key_sha256=?",
                                 (submit_key_hash,)).fetchone()
        _require(row is not None and row["execution_id_sha256"] == _hash(execution_id))
        return row

    def await_user(self, *, opportunity_key: str, requisition_id: str, profile_version: str,
                   execution_id: str, now: datetime) -> None:
        """Pause before dispatch for a challenge only the user may solve."""
        now = _utc(now)
        key_hash, _ = _identity(opportunity_key, requisition_id, profile_version)
        with self._legacy._transaction() as connection:
            row = self._row(connection, key_hash, execution_id)
            reserved = _parse_utc(row["reserved_at"])
            _require(row["status"] == RESERVED and reserved <= now <= reserved + RESERVATION_TTL)
            connection.execute("UPDATE policy_submissions SET status=?,awaiting_at=? WHERE submit_key_sha256=?",
                               (AWAITING, _time_text(now), key_hash))

    def resume(self, *, opportunity_key: str, requisition_id: str, profile_version: str,
               execution_id: str, now: datetime) -> None:
        """The user solved the challenge; the engine continues towards dispatch."""
        now = _utc(now)
        key_hash, _ = _identity(opportunity_key, requisition_id, profile_version)
        with self._legacy._transaction() as connection:
            row = self._row(connection, key_hash, execution_id)
            awaiting = _parse_utc(row["awaiting_at"])
            _require(row["status"] == AWAITING and awaiting <= now <= awaiting + AWAITING_TTL)
            _require(connection.execute("SELECT 1 FROM policy_submissions WHERE status=?",
                                        (RESERVED,)).fetchone() is None)
            connection.execute("UPDATE policy_submissions SET status=?,reserved_at=?,awaiting_at=NULL "
                               "WHERE submit_key_sha256=?", (RESERVED, _time_text(now), key_hash))

    def release(self, *, opportunity_key: str, requisition_id: str, profile_version: str,
                execution_id: str, now: datetime) -> None:
        """Abort BEFORE dispatch (drift, blocker, closed vacancy). Never after start."""
        now = _utc(now)
        key_hash, _ = _identity(opportunity_key, requisition_id, profile_version)
        with self._legacy._transaction() as connection:
            row = self._row(connection, key_hash, execution_id)
            _require(row["status"] in (RESERVED, AWAITING))
            # A stepped-back clock must never write an inconsistent row.
            floor = max(_parse_utc(row["reserved_at"]), _parse_utc(row["awaiting_at"] or row["reserved_at"]))
            connection.execute("UPDATE policy_submissions SET status=?,released_at=? WHERE submit_key_sha256=?",
                               (RELEASED, _time_text(max(now, floor)), key_hash))

    def mark_started(self, *, opportunity_key: str, requisition_id: str, profile_version: str,
                     execution_id: str, now: datetime) -> None:
        """Commit BEFORE the click. Afterwards the key is consumed forever."""
        now = _utc(now)
        key_hash, _ = _identity(opportunity_key, requisition_id, profile_version)
        with self._legacy._transaction() as connection:
            row = self._row(connection, key_hash, execution_id)
            reserved = _parse_utc(row["reserved_at"])
            _require(row["status"] == RESERVED and reserved <= now <= reserved + RESERVATION_TTL)
            if row["daily_cap"] is not None:
                started = connection.execute("SELECT COUNT(*) FROM policy_submissions WHERE started_day=?",
                                             (_local_day(now),)).fetchone()[0]
                _require(started < row["daily_cap"])
            connection.execute("UPDATE policy_submissions SET status=?,started_at=?,started_day=? "
                               "WHERE submit_key_sha256=?",
                               (STARTED, _time_text(now), _local_day(now), key_hash))

    def confirm(self, *, opportunity_key: str, requisition_id: str, profile_version: str,
                execution_id: str, evidence: ConfirmationEvidence, now: datetime) -> bool:
        """Record exact confirmation evidence; False when it does not bind."""
        now = _utc(now)
        if (not isinstance(evidence, ConfirmationEvidence) or evidence.exact_identity_match is not True
                or not isinstance(evidence.evidence_type, ConfirmationType)
                or type(evidence.evidence_ref) is not str or not _EVIDENCE.fullmatch(evidence.evidence_ref)
                or evidence.execution_id != execution_id
                or evidence.requisition_id.strip().casefold() != requisition_id.strip().casefold()):
            return False
        key_hash, _ = _identity(opportunity_key, requisition_id, profile_version)
        try:
            if evidence.submit_key != submit_identity(opportunity_key, requisition_id, profile_version):
                return False
            observed = _parse_utc(evidence.observed_at)
        except (SubmissionLedgerError, ValueError):
            return False
        if observed > now:
            return False
        with self._legacy._transaction() as connection:
            row = connection.execute("SELECT * FROM policy_submissions WHERE submit_key_sha256=?",
                                     (key_hash,)).fetchone()
            if row is None or row["execution_id_sha256"] != _hash(execution_id) \
                    or row["status"] not in (STARTED, CONFIRMED) or observed < _parse_utc(row["started_at"]):
                return False
            if row["status"] == CONFIRMED:
                return (row["evidence_ref"] == evidence.evidence_ref
                        and row["evidence_type"] == evidence.evidence_type.value)
            connection.execute(
                "UPDATE policy_submissions SET status=?,confirmed_at=?,confirmation_observed_at=?,"
                "evidence_type=?,evidence_ref=? WHERE submit_key_sha256=?",
                (CONFIRMED, _time_text(now), _time_text(observed), evidence.evidence_type.value,
                 evidence.evidence_ref, key_hash))
        return True
