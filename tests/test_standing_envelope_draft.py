"""W36-02 S4: the offline standing-envelope draft helper (publishes nothing).

Synthetic placeholders only. The round trip publishes the drafted bytes into the
S3 test approval tree (presented as root through the S3 test view), never into the
production approval root.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path

import pytest

from ejs.apps.standing_envelope_draft import __main__ as draft_cli
from ejs.services import standing_envelope_draft
from ejs.services.standing_envelope_draft import ERROR, FILES, draft_records, write_draft
from tests.test_execution_manifest import encode
from tests.test_standing_guard import ISSUED, Rig, approval_records, make_world, package_files


def records(**changes) -> tuple[bytes, bytes]:
    """Envelope and index bytes from the S3 synthetic fixture, with envelope edits."""
    world = make_world()
    files, _ = package_files(world, 3)
    _, envelope, index = approval_records([world], [files], 3, **changes)
    return envelope, index


def edited(raw: bytes, edit) -> bytes:
    document = json.loads(raw)
    edit(document)
    return encode(document)


def rejected(envelope: bytes, index: bytes) -> None:
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        draft_records(envelope, index)
    assert error.value.__cause__ is error.value.__context__ is None


def target(n: int) -> dict:
    row = deepcopy(json.loads(records()[0])["targets"][0])
    posting = f"7440000000000{n:02d}"
    row.update(posting_id=posting, requisition_id=f"REQ-SYNTH-{n:04d}",
               opportunity_key=f"opp:example:sr:{posting}",
               apply_url=row["apply_url"].replace("744000000000001", posting))
    return row


@pytest.fixture
def out(tmp_path) -> Path:
    folder = tmp_path.resolve() / "draft"
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    return folder


# --- positive control and round trip ---------------------------------------------------------

def test_pointer_pins_the_exact_owner_bytes():
    envelope, index = records()
    out_envelope, out_index, pointer = draft_records(envelope, index)
    assert (out_envelope, out_index) == (envelope, index)  # never reformatted
    assert json.loads(pointer) == dict(
        schema_version="EJS-STANDING-ENVELOPE-POINTER-1", envelope_id="env-synthetic-1",
        generation=3, envelope_sha256=hashlib.sha256(envelope).hexdigest(),
        eligibility_index_sha256=hashlib.sha256(index).hexdigest())


def test_drafted_files_published_into_the_test_root_are_admitted(tmp_path, monkeypatch, out):
    rig = Rig(tmp_path, monkeypatch)
    world = make_world()
    manifest = rig.add(world)
    _, envelope, index = approval_records(rig.worlds, rig.packages, 3)
    digests = write_draft(out, envelope, index)
    for name in FILES:  # the owner's publication, here into the synthetic tree only
        path = rig.approval / name
        path.write_bytes((out / name).read_bytes())
        os.chmod(path, 0o644)
        rig.view.register(path)
        assert digests[name] == hashlib.sha256(path.read_bytes()).hexdigest()
    rig.enqueue(manifest)
    (report,), _ = rig.run()
    assert report.status == "EMPLOYER_CONFIRMED"


def test_write_draft_creates_owner_only_files_and_never_overwrites(out):
    envelope, index = records()
    digests = write_draft(out, envelope, index)
    assert sorted(digests) == sorted(FILES)
    for name in FILES:
        info = (out / name).lstat()
        assert oct(info.st_mode & 0o777) == "0o600" and info.st_uid == os.geteuid()
    before = {name: (out / name).read_bytes() for name in FILES}
    other, _ = records(country="DE")
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(out, other, index)
    assert {name: (out / name).read_bytes() for name in FILES} == before


def test_partial_failure_removes_only_what_this_call_created(out):
    envelope, index = records()
    (out / "current.json").write_bytes(b"owner's own file")
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(out, envelope, index)
    assert sorted(p.name for p in out.iterdir()) == ["current.json"]
    assert (out / "current.json").read_bytes() == b"owner's own file"


def test_rejects_an_output_path_through_a_symlinked_ancestor(out):
    alias = out.parent / "alias"
    alias.symlink_to(out.parent, target_is_directory=True)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(alias / out.name, *records())
    assert list(out.iterdir()) == []


def test_output_modes_are_0600_with_a_restrictive_umask(out):
    previous = os.umask(0o777)
    try:
        write_draft(out, *records())
    finally:
        os.umask(previous)
    assert all((out / name).stat().st_mode & 0o777 == 0o600 for name in FILES)


def test_file_acl_check_runs_before_private_bytes_are_written(out, monkeypatch):
    def check(fd):
        info = os.fstat(fd)
        if info.st_ino != out.stat().st_ino:
            assert info.st_size == 0
            raise PermissionError

    monkeypatch.setattr(standing_envelope_draft, "_check_access_acl", check)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(out, *records())
    assert list(out.iterdir()) == []


# --- fixed limits and schema ------------------------------------------------------------------

@pytest.mark.parametrize("changes", (
    dict(status="revoked"), dict(status="draft"),
    dict(daily_cap=2), dict(daily_cap=0), dict(daily_cap=True), dict(daily_cap=1.0),
    dict(daily_timezone="UTC"), dict(ats_family="workday"), dict(policy_version="SUBMIT-2.0"),
    dict(matching_rule_version="other-rule"), dict(country="nl"), dict(country="Netherlands"),
    dict(country=None), dict(expires_at=(ISSUED + timedelta(hours=24, seconds=1)).isoformat()),
    dict(expires_at=ISSUED.isoformat()), dict(issued_at="2026-11-17 04:30"),
    dict(rollout_stages=[]), dict(rollout_stages=["R2", "R2"]), dict(rollout_stages=["R9"]),
    dict(targets=[]), dict(targets="*"), dict(template_sha256s=[]), dict(template_sha256s=["*"]),
    dict(cv_sha256="0" * 63), dict(registry_sha256=None), dict(candidate_profile_version=""),
))
def test_rejects_drafts_outside_the_fixed_limits(changes):
    rejected(*records(**changes))


def test_accepts_up_to_five_explicit_targets_and_rejects_a_sixth():
    five, index = records(targets=[target(n) for n in range(1, 6)])
    assert draft_records(five, index)
    rejected(*records(targets=[target(n) for n in range(1, 7)]))
    rejected(*records(targets=[target(1), target(1)]))  # duplicate row


@pytest.mark.parametrize("edit", (
    lambda t: t.update(apply_url="https://jobs.smartrecruiters.com/ExampleCo/*"),
    lambda t: t.update(posting_id="*"),
    lambda t: t.update(ats_family="workday"),
    lambda t: t.pop("requisition_id"),
    lambda t: t.update(extra="x"),
))
def test_rejects_wildcard_or_malformed_targets(edit):
    row = target(1)
    edit(row)
    rejected(*records(targets=[row]))


def test_rejects_identity_mismatch_and_malformed_records():
    envelope, index = records()
    rejected(envelope, edited(index, lambda d: d.update(generation=4)))
    rejected(envelope, edited(index, lambda d: d.update(envelope_id="env-other")))
    rejected(envelope, edited(index, lambda d: d.update(entries={})))
    rejected(envelope, edited(index, lambda d: d.update(entries={"exec-x": "0" * 63})))
    rejected(edited(envelope, lambda d: d.update(extra=1)), index)
    rejected(edited(envelope, lambda d: d.pop("country")), index)
    rejected(envelope.replace(b'"generation": 3', b'"generation": 3, "generation": 3'), index)
    rejected(envelope.replace(b'"generation": 3', b'"generation": 3.0'), index)
    rejected(index, envelope)  # swapped
    rejected(b"", index)
    rejected(envelope, b" " * 600_000)
    rejected("text", index)


# --- the draft directory ----------------------------------------------------------------------

def test_refuses_the_approval_root_and_its_neighbourhood(out, monkeypatch):
    envelope, index = records()
    monkeypatch.setattr(standing_envelope_draft, "_APPROVAL_ROOT", out)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(out, envelope, index)
    inner = out / "inner"
    inner.mkdir(mode=0o700)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(inner, envelope, index)
    monkeypatch.setattr(standing_envelope_draft, "_APPROVAL_ROOT", inner)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(out, envelope, index)  # a parent of the approval root
    assert sorted(p.name for p in out.iterdir()) == ["inner"] and not list(inner.iterdir())


def test_production_approval_root_is_the_refused_constant():
    assert standing_envelope_draft._APPROVAL_ROOT == Path("/Library/Application Support/EJS/approval")


def test_refuses_to_run_as_root(out, monkeypatch):
    envelope, index = records()
    monkeypatch.setattr(standing_envelope_draft.os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(out, envelope, index)
    monkeypatch.undo()
    assert not list(out.iterdir())


@pytest.mark.parametrize("damage", ("shared-mode", "symlink", "relative", "missing", "dotdot"))
def test_refuses_unsafe_draft_directories(out, damage):
    envelope, index = records()
    target_dir = out
    if damage == "shared-mode":
        os.chmod(out, 0o755)
    elif damage == "symlink":
        target_dir = out.parent / "linked"
        target_dir.symlink_to(out)
    elif damage == "relative":
        target_dir = Path("draft")
    elif damage == "missing":
        target_dir = out / "absent"
    else:
        target_dir = out / ".." / "draft"
    with pytest.raises(PermissionError, match=f"^{ERROR}$"):
        write_draft(target_dir, envelope, index)
    os.chmod(out, 0o700)
    assert not list(out.iterdir())


# --- CLI --------------------------------------------------------------------------------------

def test_cli_prints_digests_only(out, tmp_path, capsys):
    envelope, index = records()
    (tmp_path / "e.json").write_bytes(envelope)
    (tmp_path / "i.json").write_bytes(index)
    assert draft_cli.main(["--envelope", str(tmp_path / "e.json"), "--index",
                           str(tmp_path / "i.json"), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "DRAFTED" and printed["published"] is False
    assert printed["sha256"] == {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                                 for name in FILES}
    assert draft_cli.main(["--envelope", str(tmp_path / "e.json"), "--index",
                           str(tmp_path / "i.json"), "--out", str(out)]) == 2  # never overwrites
    text = capsys.readouterr().out
    assert json.loads(text) == {"status": "REJECTED"} and "synthetic" not in text
