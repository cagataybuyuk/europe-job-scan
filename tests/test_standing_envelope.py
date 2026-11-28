"""Synthetic offline coverage for the W36-02 S1 standing batch envelope.

All values are synthetic placeholders. No production path is written and the
validator output alone authorizes nothing; runtime admission is a later slice.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from ejs.services import private_source_files, standing_envelope
from ejs.services.current_fact_approval import MAX_APPROVAL_BYTES
from ejs.services.execution_manifest import MAX_MANIFEST_BYTES, form_fingerprint
from ejs.services.standing_envelope import (
    ERROR, BatchApprovalSources, BatchBinding, BatchInputs, read_trusted_batch_sources,
    validate_standing_batch,
)
from tests.test_execution_manifest import NOW, base_document, encode


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


ASSETS = dict(
    policy=b'{"synthetic":"policy"}', profile=b'{"synthetic":"profile"}',
    template=b'{"synthetic":"template"}', cv=b"%PDF-1.4 synthetic cv",
    registry=b'{"synthetic":"registry"}', inventory=b'{"synthetic":"inventory"}',
    eligibility=b'{"synthetic":"eligibility"}',
)
PINNED = ("policy", "profile", "cv", "registry", "inventory")


def manifest_document():
    doc = base_document()
    doc["policy"] = dict(version="SUBMIT-1.0", rollout_stage="R2", daily_cap=1)
    doc["cv"]["sha256"] = digest(ASSETS["cv"])
    return doc


def envelope_document():
    doc = dict(
        schema_version="EJS-STANDING-ENVELOPE-1", envelope_id="env-synthetic-1", generation=3,
        status="active", issued_at=(NOW - timedelta(hours=1)).isoformat(),
        expires_at=(NOW + timedelta(hours=23)).isoformat(), policy_version="SUBMIT-1.0",
        candidate_profile_version="profile-2026-11-16", ats_family="smartrecruiters",
        country="NL", targets=[deepcopy(manifest_document()["target"])],
        rollout_stages=["R2"], daily_cap=1, daily_timezone="Europe/Istanbul",
        matching_rule_version="match-synthetic-1", template_sha256s=[digest(ASSETS["template"])],
    )
    doc.update({f"{name}_sha256": digest(ASSETS[name]) for name in PINNED})
    return doc


def index_document():
    return dict(schema_version="EJS-BATCH-ELIGIBILITY-INDEX-1", envelope_id="env-synthetic-1",
                generation=3,
                entries={"exec-20261117-example-1": digest(ASSETS["eligibility"])})


def pointer_for(envelope: bytes, index: bytes, **override):
    doc = dict(schema_version="EJS-STANDING-ENVELOPE-POINTER-1", envelope_id="env-synthetic-1",
               generation=3, envelope_sha256=digest(envelope),
               eligibility_index_sha256=digest(index))
    doc.update(override)
    return doc


def bundle(envelope=None, index=None, manifest=None, **inputs):
    """Return pinned sources and inputs; the pointer always re-pins edited records."""
    envelope_raw = envelope if isinstance(envelope, bytes) else encode(envelope or envelope_document())
    index_raw = index if isinstance(index, bytes) else encode(index or index_document())
    sources = BatchApprovalSources(encode(pointer_for(envelope_raw, index_raw)), envelope_raw,
                                   index_raw)
    manifest_raw = manifest if isinstance(manifest, bytes) else encode(manifest or manifest_document())
    values = dict(ASSETS, manifest=manifest_raw, country="NL",
                  matching_rule_version="match-synthetic-1")
    values.update(inputs)
    return sources, BatchInputs(**values)


def reject(sources, inputs, now=NOW):
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        validate_standing_batch(sources, inputs, now=now)
    assert error.value.__cause__ is error.value.__context__ is None
    return error.value


def edited_envelope(**changes):
    doc = envelope_document()
    doc.update(changes)
    return doc


# --- pure validation: positive control -------------------------------------------------

def test_accepts_exact_synthetic_batch_and_returns_binding_only():
    sources, inputs = bundle()
    binding = validate_standing_batch(sources, inputs, now=NOW)
    assert binding == BatchBinding("env-synthetic-1", 3, digest(sources.envelope),
                                   digest(sources.eligibility_index))
    assert "synthetic" not in repr(binding) and "synthetic" not in repr(inputs)


def test_errors_do_not_echo_values():
    sources, inputs = bundle(country="SECRET-COUNTRY")
    error = reject(sources, inputs)
    assert str(error) == ERROR and "SECRET" not in repr(error.args)


# --- pointer binding, identity and rollback --------------------------------------------

@pytest.mark.parametrize("name", ("envelope", "eligibility_index"))
def test_rejects_any_byte_change_not_pinned_by_pointer(name):
    sources, inputs = bundle()
    changed = replace(sources, **{name: getattr(sources, name) + b"\n"})
    reject(changed, inputs)


@pytest.mark.parametrize("override", (
    dict(envelope_id="env-synthetic-2"), dict(generation=4), dict(generation=2),
))
def test_rejects_pointer_identity_not_matching_records(override):
    sources, inputs = bundle()
    pointer = pointer_for(sources.envelope, sources.eligibility_index, **override)
    reject(replace(sources, pointer=encode(pointer)), inputs)


def test_rejects_rollback_of_one_record_to_an_older_generation():
    old_envelope = encode(edited_envelope(generation=2))
    sources, inputs = bundle()
    # The current pointer names generation 3; an older envelope cannot be swapped in,
    # even when re-pinned, because every record must carry the same generation.
    pointer = pointer_for(old_envelope, sources.eligibility_index)
    reject(BatchApprovalSources(encode(pointer), old_envelope, sources.eligibility_index), inputs)


@pytest.mark.parametrize("record", ("envelope", "index"))
def test_rejects_record_identity_mismatch_even_when_repinned(record):
    if record == "envelope":
        sources, inputs = bundle(envelope=edited_envelope(envelope_id="env-synthetic-2"))
    else:
        sources, inputs = bundle(index=dict(index_document(), generation=4))
    reject(sources, inputs)


# --- strict JSON ----------------------------------------------------------------------

MALFORMED = (
    b"", b"not json", b"\xff\xfe", b"[]", b"null", b'{"a":1}{"b":2}',
    b'{"schema_version":"x","schema_version":"y"}', b'{"generation":NaN}', b'{"generation":1.0}',
)


@pytest.mark.parametrize("raw", MALFORMED)
@pytest.mark.parametrize("record", ("pointer", "envelope", "eligibility_index"))
def test_rejects_malformed_or_duplicate_key_records(record, raw):
    sources, inputs = bundle()
    if record == "pointer":
        sources = replace(sources, pointer=raw)
    else:
        sources = replace(sources, **{record: raw})
        envelope, index = sources.envelope, sources.eligibility_index
        sources = replace(sources, pointer=encode(pointer_for(envelope, index)) if raw else b"{}")
    reject(sources, inputs)


def test_rejects_duplicate_key_inside_envelope_even_when_repinned():
    raw = encode(envelope_document())[:-1] + b',"daily_cap":1}'
    sources, inputs = bundle(envelope=raw)
    reject(sources, inputs)


@pytest.mark.parametrize("record", ("pointer", "envelope", "index"))
@pytest.mark.parametrize("change", ("unknown", "missing", "schema"))
def test_rejects_unknown_missing_keys_and_wrong_schema(record, change):
    docs = {"envelope": envelope_document(), "index": index_document()}
    if record == "pointer":
        sources, inputs = bundle()
        doc = pointer_for(sources.envelope, sources.eligibility_index)
    else:
        doc = docs[record]
    if change == "unknown":
        doc["approved"] = True
    elif change == "missing":
        doc.pop("generation")
    else:
        doc["schema_version"] = "EJS-CURRENT-FACT-APPROVAL-1"
    if record == "pointer":
        reject(replace(sources, pointer=encode(doc)), inputs)
    else:
        reject(*bundle(**{record: doc}))


def test_rejects_oversized_sources():
    sources, inputs = bundle()
    reject(replace(sources, pointer=sources.pointer.ljust(MAX_APPROVAL_BYTES + 1)), inputs)
    padded = sources.envelope.ljust(MAX_MANIFEST_BYTES + 1)
    reject(BatchApprovalSources(encode(pointer_for(padded, sources.eligibility_index)), padded,
                                sources.eligibility_index), inputs)


# --- envelope scope, caps and bounds ---------------------------------------------------

@pytest.mark.parametrize("changes", (
    dict(daily_cap=True), dict(daily_cap=2), dict(daily_cap=0), dict(daily_cap=None),
    dict(daily_cap="1"), dict(daily_cap=1.0),
    dict(generation=True), dict(generation=0), dict(generation=2**63),
    dict(status="revoked"), dict(status="draft"), dict(policy_version="SUBMIT-2.0"),
    dict(ats_family="greenhouse"), dict(daily_timezone="UTC"),
    dict(envelope_id=""), dict(envelope_id="*"),
    dict(candidate_profile_version="profile-other"),
    dict(rollout_stages=[]), dict(rollout_stages=["R4"]), dict(rollout_stages=["R2", "R2"]),
    dict(rollout_stages=["R3"]), dict(rollout_stages="R2"), dict(rollout_stages=["*"]),
))
def test_rejects_scope_cap_and_identity_changes(changes):
    doc = edited_envelope(**changes)
    if "generation" in changes:
        index = dict(index_document(), generation=changes["generation"])
        sources, inputs = bundle(envelope=doc, index=index)
        pointer = pointer_for(sources.envelope, sources.eligibility_index,
                              generation=changes["generation"])
        reject(replace(sources, pointer=encode(pointer)), inputs)
    else:
        reject(*bundle(envelope=doc))


@pytest.mark.parametrize("country", (None, "", "XX", "nl", "Netherlands", "*", ["NL"]))
def test_rejects_unconfigured_or_ambiguous_envelope_country(country):
    reject(*bundle(envelope=edited_envelope(country=country)))


@pytest.mark.parametrize("country", (None, "", "DE", "nl"))
def test_rejects_vacancy_country_outside_envelope(country):
    reject(*bundle(country=country))


@pytest.mark.parametrize("rule", (None, "", "match-synthetic-2"))
def test_rejects_matching_rule_mismatch(rule):
    reject(*bundle(matching_rule_version=rule))


def _target(**changes):
    target = deepcopy(manifest_document()["target"])
    target.update(changes)
    return target


@pytest.mark.parametrize("targets", (
    None, [], "*", ["*"], [{}],
    [_target(posting_id="744000000000002",
             apply_url="https://jobs.smartrecruiters.com/ExampleCo/744000000000002")],
    [_target(company="OtherCo")], [_target(requisition_id="REF002")],
    [_target(opportunity_key="opp:other")], [_target(), _target()],
    [_target(ats_family="greenhouse")], [dict(_target(), extra=True)],
))
def test_rejects_unconfigured_wildcard_or_non_matching_targets(targets):
    reject(*bundle(envelope=edited_envelope(targets=targets)))


def test_accepts_manifest_target_among_several_exact_targets():
    other = _target(posting_id="744000000000002", opportunity_key="opp:other",
                    apply_url="https://jobs.smartrecruiters.com/ExampleCo/744000000000002")
    sources, inputs = bundle(envelope=edited_envelope(targets=[other, _target()]))
    assert validate_standing_batch(sources, inputs, now=NOW).generation == 3


# --- time -----------------------------------------------------------------------------

@pytest.mark.parametrize("now", (
    NOW - timedelta(hours=1, seconds=1),          # before issue
    NOW + timedelta(hours=23),                    # at expiry
    NOW + timedelta(days=2),                      # long expired
    NOW.replace(tzinfo=None),                     # naive clock
    NOW.astimezone(timezone(timedelta(hours=3))), # non-UTC clock
))
def test_rejects_clock_outside_fresh_window(now):
    reject(*bundle(), now=now)


def test_rejects_window_longer_than_24_hours_or_inverted():
    reject(*bundle(envelope=edited_envelope(expires_at=(NOW + timedelta(hours=23, seconds=1)).isoformat())))
    reject(*bundle(envelope=edited_envelope(expires_at=(NOW - timedelta(hours=2)).isoformat())))
    reject(*bundle(envelope=edited_envelope(issued_at="2026-11-17T04:30:00")))


def test_rejects_manifest_that_outlives_envelope():
    reject(*bundle(envelope=edited_envelope(
        issued_at=(NOW - timedelta(hours=10)).isoformat(),
        expires_at=(NOW + timedelta(hours=10)).isoformat())))


# --- manifest subset ------------------------------------------------------------------

def _manifest(**policy):
    doc = manifest_document()
    doc["policy"].update(policy)
    return doc


@pytest.mark.parametrize("doc", (
    _manifest(rollout_stage="R3", daily_cap=5),
    _manifest(rollout_stage="R3", daily_cap=1),
    _manifest(rollout_stage="R4", daily_cap=None),
    dict(manifest_document(), **{"pass": "observe"}),
    dict(manifest_document(), candidate_profile_version="profile-2026-11-17",
         submit_key="submit:opp:example:sr:744000000000001:REF001:profile-2026-11-17"),
))
def test_rejects_manifest_outside_envelope_subset(doc):
    reject(*bundle(manifest=doc))


def test_rejects_invalid_or_empty_manifest():
    reject(*bundle(manifest=b"{}"))
    reject(*bundle(manifest=b""))


# --- exact pins -----------------------------------------------------------------------

@pytest.mark.parametrize("name", (*PINNED, "template", "eligibility"))
def test_rejects_each_changed_input(name):
    reject(*bundle(**{name: ASSETS[name] + b" "}))


@pytest.mark.parametrize("name", (*PINNED, "template", "eligibility"))
@pytest.mark.parametrize("value", (b"", None, "text"))
def test_rejects_each_unconfigured_input(name, value):
    reject(*bundle(**{name: value}))


@pytest.mark.parametrize("name", PINNED)
@pytest.mark.parametrize("pin", (None, "", "*", "A" * 64, "0" * 63))
def test_rejects_missing_wildcard_or_malformed_pin(name, pin):
    reject(*bundle(envelope=edited_envelope(**{f"{name}_sha256": pin})))


def test_rejects_cv_matching_envelope_but_not_manifest():
    other = b"%PDF-1.4 other synthetic cv"
    reject(*bundle(envelope=edited_envelope(cv_sha256=digest(other)), cv=other))


@pytest.mark.parametrize("templates", (
    [], None, "*", ["*"], [digest(b"other template")],
    [digest(ASSETS["template"]), digest(ASSETS["template"])],
))
def test_rejects_unlisted_wildcard_or_duplicate_templates(templates):
    reject(*bundle(envelope=edited_envelope(template_sha256s=templates)))


@pytest.mark.parametrize("entries", (
    {}, None, [], {"exec-other": digest(ASSETS["eligibility"])},
    {"exec-20261117-example-1": "*"}, {"*": digest(ASSETS["eligibility"])},
    {"exec-20261117-example-1": digest(ASSETS["eligibility"]), "": "0" * 64},
))
def test_rejects_unindexed_or_malformed_eligibility_entries(entries):
    reject(*bundle(index=dict(index_document(), entries=entries)))


def test_validator_is_pure_and_does_not_touch_the_filesystem(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("filesystem access during pure validation")
    for name in ("open", "stat", "lstat", "listdir", "scandir"):
        monkeypatch.setattr(os, name, forbidden)
    sources, inputs = bundle()
    assert validate_standing_batch(sources, inputs, now=NOW)


# --- trusted source reader -------------------------------------------------------------

def test_production_reader_uses_fixed_root_without_overrides(monkeypatch, tmp_path):
    assert standing_envelope._APPROVAL_ROOT == Path("/Library/Application Support/EJS/approval")
    source = inspect.getsource(standing_envelope)
    assert "environ" not in source and "getenv" not in source and "geteuid" not in source
    for name in ("EJS_APPROVAL_ROOT", "EJS_TRUSTED_UID", "HOME", "TMPDIR"):
        monkeypatch.setenv(name, str(tmp_path))
    if Path("/Library/Application Support/EJS/approval").exists():
        pytest.skip("a real approval root exists on this host; not touched by tests")
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        read_trusted_batch_sources()
    assert error.value.__cause__ is error.value.__context__ is None


class _AsRootStat:
    """Present the caller's own entries as uid 0; every other field is real."""

    def __init__(self, info):
        self._info = info

    def __getattr__(self, name):
        value = getattr(self._info, name)
        return 0 if name == "st_uid" and value == os.geteuid() else value


class _RootOwnedView:
    """Test-only os stand-in for the reader module; never used by production code."""

    def __getattr__(self, name):
        return getattr(os, name)

    @staticmethod
    def stat(*args, **kwargs):
        return _AsRootStat(os.stat(*args, **kwargs))

    @staticmethod
    def fstat(fd):
        return _AsRootStat(os.fstat(fd))


NAMES = ("current.json", "envelope.json", "eligibility-index.json")


def read_root_owned(paths):
    return private_source_files._read_sources(
        tuple((path, MAX_APPROVAL_BYTES) for path in paths), root_owned=True)


def _unsuitable_ancestor(path: Path) -> bool:
    """Whether a real directory from ``path`` up to / can never pass the root walk.

    Under the test view the caller's uid stands in for root, so only a
    symlink, a foreign owner, group/other write or an allow ACL disqualifies.
    """
    for directory in (path, *path.parents):
        info = directory.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or info.st_mode & 0o022):
            return True
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            private_source_files._check_access_acl(fd)
        except PermissionError:
            return True
        finally:
            os.close(fd)
    return False


def _establish_root_tree(tmp_path, monkeypatch):
    approval = tmp_path.resolve() / "synthetic-approval"
    approval.mkdir()
    approval.chmod(0o755)
    paths = tuple(approval / name for name in NAMES)
    for path in paths:
        path.write_bytes(b'{"synthetic":true}')
        path.chmod(0o644)
    # Skip only for the established ancestor condition; any other failure of
    # the positive control must surface as an error.
    if _unsuitable_ancestor(approval):
        pytest.skip("temporary root has shared/writable ancestors; run with --basetemp "
                    "under a non-shared directory to exercise root-policy negatives")
    monkeypatch.setattr(private_source_files, "os", _RootOwnedView())
    assert read_root_owned(paths) == (b'{"synthetic":true}',) * 3
    return paths


@pytest.fixture
def root_tree(tmp_path, monkeypatch):
    return _establish_root_tree(tmp_path, monkeypatch)


def test_unsuitable_ancestor_detects_only_real_disqualifiers(tmp_path):
    base = tmp_path.resolve()
    shared = base / "synthetic-shared"
    child = shared / "synthetic-child"
    child.mkdir(parents=True, mode=0o755)
    shared.chmod(0o777)
    try:
        assert _unsuitable_ancestor(child)
    finally:
        shared.chmod(0o755)
    # Restored, the child adds nothing beyond whatever its real ancestors have.
    assert _unsuitable_ancestor(child) == _unsuitable_ancestor(base)
    link = base / "synthetic-link"
    link.symlink_to(shared)
    assert _unsuitable_ancestor(link)


def test_root_tree_surfaces_positive_control_errors_instead_of_skipping(tmp_path, monkeypatch):
    if _unsuitable_ancestor(tmp_path.resolve()):
        pytest.skip("needs --basetemp under a non-shared directory")

    def broken(_source):
        raise PermissionError("synthetic positive-control failure")
    monkeypatch.setattr(private_source_files, "_read_exact", broken)
    try:
        with pytest.raises(PermissionError, match="synthetic positive-control failure"):
            _establish_root_tree(tmp_path, monkeypatch)
    except pytest.skip.Exception:
        pytest.fail("a positive-control error was skipped instead of surfacing")


def reject_read(paths):
    # The low-level walk fails closed with any OSError; the public reader maps
    # every one of them to the single opaque error (see the wrapper test).
    with pytest.raises(OSError):
        read_root_owned(paths)


@pytest.mark.parametrize("damage", (None, "missing", "symlink", "mode"))
def test_public_reader_returns_sources_or_one_opaque_error(root_tree, monkeypatch, damage):
    # Test-only relocation of the fixed root; production has no override path.
    monkeypatch.setattr(standing_envelope, "_APPROVAL_ROOT", root_tree[0].parent)
    if damage is None:
        assert read_trusted_batch_sources() == BatchApprovalSources(*(b'{"synthetic":true}',) * 3)
        return
    target = root_tree[1]
    if damage == "missing":
        target.unlink()
    elif damage == "symlink":
        target.symlink_to(target.rename(target.with_suffix(".orig")))
    else:
        target.chmod(0o666)
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        read_trusted_batch_sources()
    assert error.value.__cause__ is error.value.__context__ is None


def test_root_policy_rejects_caller_owned_files_without_view(tmp_path):
    approval = tmp_path.resolve() / "synthetic-approval"
    approval.mkdir(mode=0o755)
    paths = tuple(approval / name for name in NAMES)
    for path in paths:
        path.write_bytes(b"{}")
        path.chmod(0o644)
    reject_read(paths)  # wrong uid: the caller, not root


class _FilesOnlyAsRootStat(_AsRootStat):
    def __getattr__(self, name):
        if name == "st_uid" and not stat.S_ISREG(self._info.st_mode):
            return self._info.st_uid
        return super().__getattr__(name)


class _FilesOnlyRootView(_RootOwnedView):
    @staticmethod
    def stat(*args, **kwargs):
        return _FilesOnlyAsRootStat(os.stat(*args, **kwargs))

    @staticmethod
    def fstat(fd):
        return _FilesOnlyAsRootStat(os.fstat(fd))


def test_root_policy_rejects_caller_owned_directories(root_tree, monkeypatch):
    # Files look root-owned, but their caller-owned ancestors must still reject.
    monkeypatch.setattr(private_source_files, "os", _FilesOnlyRootView())
    reject_read(root_tree)


@pytest.mark.parametrize("mode", (0o600, 0o664, 0o646, 0o666, 0o755, 0o4644))
def test_root_policy_rejects_file_modes(root_tree, mode):
    root_tree[1].chmod(mode)
    reject_read(root_tree)


@pytest.mark.parametrize("mode", (0o775, 0o757, 0o777, 0o1777))
def test_root_policy_rejects_writable_or_shared_parent(root_tree, mode):
    root_tree[0].parent.chmod(mode)
    try:
        reject_read(root_tree)
    finally:
        root_tree[0].parent.chmod(0o755)


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "fifo", "directory", "missing", "empty"))
def test_root_policy_rejects_ineligible_entries(root_tree, kind):
    target = root_tree[2]
    original = target.rename(target.with_suffix(".orig"))
    if kind == "symlink":
        target.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, target)
    elif kind == "fifo":
        os.mkfifo(target, mode=0o644)
    elif kind == "directory":
        target.mkdir(mode=0o755)
    elif kind == "empty":
        target.write_bytes(b"")
        target.chmod(0o644)
    reject_read(root_tree)


def test_root_policy_rejects_symlinked_parent(root_tree):
    parent = root_tree[0].parent
    moved = parent.rename(parent.with_name("synthetic-real"))
    parent.symlink_to(moved)
    reject_read(root_tree)


def test_root_policy_rejects_oversized_file(root_tree, monkeypatch):
    with root_tree[0].open("ab") as stream:
        stream.truncate(MAX_APPROVAL_BYTES + 1)
    monkeypatch.setattr(private_source_files, "_read_exact",
                        lambda _: pytest.fail("unexpected read"))
    reject_read(root_tree)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin extended ACL grant")
@pytest.mark.parametrize("on", ("file", "parent"))
def test_root_policy_rejects_acl_grant(root_tree, on):
    path = root_tree[0] if on == "file" else root_tree[0].parent
    grant = "everyone allow write" if on == "file" else "everyone allow add_file"
    subprocess.run(["/bin/chmod", "+a", grant, path], check=True, capture_output=True)
    reject_read(root_tree)


def test_root_policy_rejects_file_replaced_during_read(root_tree, monkeypatch):
    read_exact = private_source_files._read_exact

    def replace_first(source):
        raw = read_exact(source)
        if source.name == NAMES[-1]:
            original = root_tree[0].rename(root_tree[0].with_suffix(".orig"))
            root_tree[0].write_bytes(original.read_bytes())
            root_tree[0].chmod(0o644)
        return raw

    monkeypatch.setattr(private_source_files, "_read_exact", replace_first)
    reject_read(root_tree)


def _real_root_file():
    for candidate in ("/private/etc/hosts", "/etc/hosts"):
        path = Path(candidate)
        try:
            info = path.lstat()
        except OSError:
            continue
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) in (0o440, 0o444, 0o640, 0o644)):
            continue
        ancestors = [Path(*path.parts[:end]) for end in range(1, len(path.parts))]
        if all(not a.is_symlink() and a.stat().st_uid == 0
               and not a.stat().st_mode & 0o022 for a in ancestors):
            return path
    return None


@pytest.mark.skipif(_real_root_file() is None, reason="no root-owned single-link file found")
def test_root_policy_reads_a_real_root_owned_file_without_any_view():
    path = _real_root_file()
    assert read_root_owned((path,)) == (path.read_bytes(),)
