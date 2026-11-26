"""Synthetic tests of the offline current approval boundary."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from ejs.services.current_fact_approval import ERROR, SCHEMA, resolve_current_fact_inputs

NOW = datetime(2026, 11, 26, 7, 0, tzinfo=timezone.utc)
TEMPLATE = b'{"synthetic":"template"}'
REGISTRY = b'{"synthetic":"registry"}'
SNAPSHOT = b'{"synthetic":"snapshot"}'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(doc):
    return json.dumps(doc, sort_keys=True).encode()


def approval():
    return dict(schema_version=SCHEMA, generation=7, status="active",
                profile_version="synthetic-v1", policy_version="SUBMIT-1.0",
                issued_at=(NOW - timedelta(minutes=10)).isoformat(),
                expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                template_sha256=digest(TEMPLATE), registry_sha256=digest(REGISTRY),
                snapshot_sha256=digest(SNAPSHOT))


def resolve(doc=None, *, raw=None, template=TEMPLATE, registry=REGISTRY, snapshot=SNAPSHOT,
            pin=None, generation=7, profile="synthetic-v1", now=NOW):
    if raw is None:
        raw = encode(approval() if doc is None else doc)
    return resolve_current_fact_inputs(
        raw, template, registry, snapshot,
        expected_approval_sha256=digest(raw) if pin is None else pin,
        expected_generation=generation, expected_profile_version=profile, now=now)


def reject(*args, **kwargs):
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as caught:
        resolve(*args, **kwargs)
    assert caught.value.__context__ is None


def test_current_independently_pinned_approval_returns_immutable_pins():
    pins = resolve()
    assert pins.template_sha256 == digest(TEMPLATE)
    assert pins.registry_sha256 == digest(REGISTRY)
    assert pins.snapshot_sha256 == digest(SNAPSHOT)
    assert digest(REGISTRY) not in repr(pins)
    with pytest.raises(FrozenInstanceError):
        pins.registry_sha256 = "other"


def test_rollback_with_valid_historical_pin_rejected_against_current_generation():
    old = approval()
    old["generation"] = 6
    reject(old, generation=7)


def test_latest_revocation_blocks_even_with_valid_current_pin():
    revoked = approval()
    revoked.update(status="revoked", generation=8)
    reject(revoked, generation=8)
    reject(approval(), generation=8)


def test_latest_pin_rejects_prior_record():
    new = approval()
    new.update(status="revoked", generation=8)
    reject(approval(), pin=digest(encode(new)), generation=8)


@pytest.mark.parametrize("key,value", [
    ("schema_version", "other"), ("status", "pending"), ("status", "revoked"),
    ("profile_version", "wrong-profile"), ("policy_version", "SUBMIT-0.9"),
    ("generation", True), ("generation", 7.0), ("generation", 0),
    ("issued_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("expires_at", NOW.isoformat()),
    ("expires_at", (NOW + timedelta(days=2)).isoformat()),
    ("issued_at", "2026-11-26T06:50:00"),
    ("registry_sha256", "0" * 64), ("snapshot_sha256", "0" * 64),
    ("registry_sha256", None), ("template_sha256", None),
    ("template_sha256", "0" * 64),
])
def test_invalid_approval_fails_closed(key, value):
    doc = approval()
    doc[key] = value
    reject(doc)


@pytest.mark.parametrize("kwargs", [
    {"pin": ""}, {"pin": "0" * 64}, {"generation": True}, {"generation": 8},
    {"profile": ""}, {"profile": "synthetic-v2"}, {"registry": b"changed"},
    {"snapshot": b"changed"}, {"registry": b""}, {"snapshot": b""},
    {"template": b"changed"}, {"template": b""},
    {"now": NOW.replace(tzinfo=None)},
])
def test_external_context_and_exact_bytes_required(kwargs):
    reject(**kwargs)


@pytest.mark.parametrize("raw", [
    b"", b"null", b"[]", b"\xff", b" " * 16385,
    b'{"status":"active","status":"revoked"}',
    b'{"generation":NaN}', b'{"generation":Infinity}',
])
def test_malformed_approval(raw):
    reject(raw=raw)


def test_unknown_or_missing_keys_reject():
    doc = approval()
    doc["extra"] = "not-reviewed"
    reject(doc)
    del doc["extra"]
    del doc["status"]
    reject(doc)


def test_approval_expires_at_boundary():
    doc = approval()
    reject(doc, now=NOW + timedelta(minutes=10))

def test_missing_template_pin_rejects_legacy_two_pin_record():
    doc = approval()
    del doc["template_sha256"]
    reject(doc)


def test_template_bytes_cannot_be_swapped_with_registry():
    reject(template=REGISTRY, registry=TEMPLATE)


def test_public_traceback_omits_private_inputs_and_pins():
    with pytest.raises(PermissionError) as caught:
        resolve(template=b"unapproved synthetic template")
    tb = caught.value.__traceback__
    while tb.tb_next is not None:
        tb = tb.tb_next
    locals_ = tb.tb_frame.f_locals
    for key in ("approval_raw", "template_raw", "registry_raw", "snapshot_raw",
                "expected_approval_sha256", "expected_profile_version"):
        assert key not in locals_
    for key in ("approval", "template_pin", "registry_pin", "snapshot_pin"):
        assert locals_.get(key) is None
