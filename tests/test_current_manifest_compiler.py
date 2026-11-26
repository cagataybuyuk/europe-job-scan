"""Synthetic end-to-end tests of the three W3.6 offline gates."""
from datetime import timedelta
import hashlib
import json

import pytest

from ejs.services.current_fact_approval import SCHEMA
from ejs.services.current_manifest_compiler import ERROR, compile_current_manifest
from ejs.services.execution_manifest import load_execution_manifest
from tests.test_reviewed_manifest_compiler import inputs
from tests.test_execution_manifest import NOW, encode


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture_inputs():
    doc, template, registry, snapshot = inputs()
    raws = tuple(map(encode, (template, registry, snapshot)))
    profile = template["candidate_profile_version"]
    approval = dict(schema_version=SCHEMA, generation=11, status="active",
                    profile_version=profile, policy_version="SUBMIT-1.0",
                    issued_at=(NOW - timedelta(minutes=5)).isoformat(),
                    expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                    template_sha256=digest(raws[0]), registry_sha256=digest(raws[1]),
                    snapshot_sha256=digest(raws[2]))
    raw = encode(approval)
    context = dict(expected_approval_sha256=digest(raw), expected_generation=11,
                   expected_profile_version=profile, now=NOW)
    return doc, approval, raws, context


def compile_(approval, raws, context):
    return compile_current_manifest(encode(approval), *raws, **context)


def reject(approval, raws, context):
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as exc:
        compile_(approval, raws, context)
    assert exc.value.__context__ is None
    assert exc.value.__cause__ is None


def repin(approval, context):
    """Synthetic independent reviewer only; never derive a real pin this way."""
    return dict(context, expected_approval_sha256=digest(encode(approval)))


def test_complete_offline_chain():
    doc, approval, raws, ctx = fixture_inputs()
    compiled = compile_(approval, raws, ctx)
    assert json.loads(compiled) == doc
    assert load_execution_manifest(compiled, now=NOW).candidate_profile_version == ctx[
        "expected_profile_version"]
    assert compile_(approval, raws, ctx) == compiled


@pytest.mark.parametrize("index", range(3), ids=["template", "registry", "snapshot"])
def test_each_changed_input_rejected_before_compilation(index):
    _, approval, raws, ctx = fixture_inputs()
    edited = list(raws)
    edited[index] += b"\n"
    reject(approval, edited, ctx)


def test_current_profile_cannot_differ_from_all_approved_inputs():
    _, approval, raws, ctx = fixture_inputs()
    approval["profile_version"] = "synthetic-other"
    ctx["expected_profile_version"] = "synthetic-other"
    reject(approval, raws, repin(approval, ctx))


def test_revocation_and_rollback():
    _, approval, raws, ctx = fixture_inputs()
    revoked = dict(approval, status="revoked", generation=12)
    newer = repin(revoked, dict(ctx, expected_generation=12))
    reject(revoked, raws, newer)
    reject(approval, raws, newer)
    # Even with the historical digest, the independently current generation
    # rejects the historical approval.
    reject(approval, raws, dict(ctx, expected_generation=12))


def test_expiry_requires_fresh_state_not_reuse_of_compiled_bytes():
    _, approval, raws, ctx = fixture_inputs()
    assert compile_(approval, raws, ctx)
    reject(approval, raws, dict(ctx, now=NOW + timedelta(minutes=10)))


@pytest.mark.parametrize("index,change", [
    (0, lambda t: t["form"]["fields"][0]["answer"].update(value="unreviewed")),
    (1, lambda r: r["facts"][0].update(verified=False)),
    (2, lambda s: s["facts"][0].update(provenance="different-source")),
])
def test_matching_approval_pins_do_not_override_downstream_denials(index, change):
    _, approval, raws, ctx = fixture_inputs()
    edited = list(raws)
    obj = json.loads(edited[index])
    change(obj)
    edited[index] = encode(obj)
    approval[("template_sha256", "registry_sha256", "snapshot_sha256")[index]] = digest(
        edited[index])
    reject(approval, edited, repin(approval, ctx))


def test_error_frame_does_not_retain_private_inputs():
    _, approval, raws, ctx = fixture_inputs()
    with pytest.raises(PermissionError) as exc:
        compile_(approval, raws, dict(ctx, expected_generation=99))
    tb = exc.value.__traceback__
    while tb.tb_next is not None:
        tb = tb.tb_next
    assert tb.tb_frame.f_code.co_name == "compile_current_manifest"
    for key in ("approval_raw", "template_raw", "registry_raw", "snapshot_raw",
                "expected_approval_sha256", "expected_profile_version"):
        assert key not in tb.tb_frame.f_locals
    for key in ("pins", "compiled", "manifest"):
        assert tb.tb_frame.f_locals.get(key) is None
