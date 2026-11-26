"""Synthetic local filesystem coverage for the W3.6 offline compiler."""
import errno
import json
import os
import stat
import struct
import subprocess
import sys

import pytest

from ejs.services import private_source_files
from ejs.services.current_fact_approval import (
    MAX_APPROVAL_BYTES, MAX_REGISTRY_BYTES, MAX_SNAPSHOT_BYTES,
)
from ejs.services.current_manifest_compiler import ERROR, compile_current_manifest_from_files
from ejs.services.execution_manifest import MAX_MANIFEST_BYTES
from tests.test_current_manifest_compiler import digest, fixture_inputs, repin
from tests.test_execution_manifest import encode


NAMES = ("approval", "template", "registry", "snapshot")
LIMITS = (MAX_APPROVAL_BYTES, MAX_MANIFEST_BYTES, MAX_REGISTRY_BYTES, MAX_SNAPSHOT_BYTES)


@pytest.fixture
def sources(tmp_path):
    doc, approval, raws, context = fixture_inputs()
    # macOS exposes some system temporary directories through a symlink alias.
    ancestor = tmp_path.resolve() / "synthetic-tree"
    ancestor.mkdir(mode=0o700)
    parent = ancestor / "synthetic-private"
    parent.mkdir(mode=0o700)
    paths = tuple(parent / f"{name}.json" for name in NAMES)
    for path, raw in zip(paths, (encode(approval), *raws), strict=True):
        path.write_bytes(raw)
        path.chmod(0o600)
    return doc, paths, context


def reject(paths, context):
    with pytest.raises(PermissionError, match=f"^{ERROR}$") as error:
        compile_current_manifest_from_files(*paths, **context)
    assert error.value.__cause__ is error.value.__context__ is None
    frame = error.value.__traceback__.tb_next
    assert frame.tb_next is None
    assert frame.tb_frame.f_code is compile_current_manifest_from_files.__code__
    assert not frame.tb_frame.f_locals


def test_accepts_exact_pinned_bytes_at_each_limit(sources):
    doc, paths, context = sources
    approval = json.loads(paths[0].read_bytes())
    for name, path, limit in zip(NAMES[1:], paths[1:], LIMITS[1:], strict=True):
        padded = path.read_bytes().ljust(limit, b" ")
        path.write_bytes(padded)
        approval[f"{name}_sha256"] = digest(padded)
    padded_approval = encode(approval).ljust(MAX_APPROVAL_BYTES, b" ")
    paths[0].write_bytes(padded_approval)
    context["expected_approval_sha256"] = digest(padded_approval)
    compiled = compile_current_manifest_from_files(*map(str, paths), **context)
    assert json.loads(compiled) == doc
    reject(paths, dict(context, expected_generation=context["expected_generation"] + 1))


@pytest.mark.parametrize("index", range(4), ids=NAMES)
def test_rejects_each_byte_change_even_in_valid_json(sources, index):
    _, paths, context = sources
    with paths[index].open("ab") as stream:
        stream.write(b"\n")
    reject(paths, context)


@pytest.mark.parametrize("index", range(4), ids=NAMES)
def test_rejects_each_oversized_file_before_reading(sources, monkeypatch, index):
    _, paths, context = sources
    with paths[index].open("ab") as stream:
        stream.truncate(LIMITS[index] + 1)
    monkeypatch.setattr(private_source_files, "_read_exact", lambda _: pytest.fail("unexpected read"))
    reject(paths, context)


@pytest.mark.parametrize("index", range(4), ids=NAMES)
def test_rejects_each_malformed_source_even_with_matching_pins(sources, index):
    _, paths, context = sources
    approval = json.loads(paths[0].read_bytes())
    raw = b'{"synthetic-private":"\xff"}'
    if index == 0:
        context["expected_approval_sha256"] = digest(raw)
    else:
        approval[f"{NAMES[index]}_sha256"] = digest(raw)
        paths[0].write_bytes(encode(approval))
        context = repin(approval, context)
    paths[index].write_bytes(raw)
    reject(paths, context)


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "fifo", "directory", "missing", "empty"))
def test_rejects_ineligible_file_entries(sources, kind):
    _, paths, context = sources
    target = paths[0]
    original = target.rename(target.with_suffix(".orig"))
    if kind == "symlink":
        target.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, target)
    elif kind == "fifo":
        os.mkfifo(target, mode=0o600)
    elif kind == "directory":
        target.mkdir(mode=0o700)
    elif kind == "empty":
        target.touch(mode=0o600)
    reject(paths, context)


def test_rejects_unsafe_modes_on_file_parent_and_ancestor(sources):
    _, paths, context = sources
    target = paths[0]
    for path, unsafe, safe in ((target, 0o640, 0o600), (target.parent, 0o750, 0o700),
                               (target.parent.parent, 0o770, 0o700)):
        path.chmod(unsafe)
        reject(paths, context)
        path.chmod(safe)
    assert compile_current_manifest_from_files(*paths, **context)


def test_rejects_relative_traversal_and_ancestor_symlink(sources):
    _, paths, context = sources
    target = paths[0]
    ancestor = target.parent.parent
    alias = ancestor.with_suffix(".alias")
    alias.symlink_to(ancestor, target_is_directory=True)
    for path in (target.name, target.parent / ".." / target.parent.name / target.name,
                 alias / target.parent.name / target.name):
        reject((path, *paths[1:]), context)


@pytest.mark.parametrize("on", ("file", "parent", "ancestor"))
def test_rejects_acl_even_with_safe_unix_mode(sources, on):
    _, paths, context = sources
    path = {"file": paths[0], "parent": paths[0].parent, "ancestor": paths[0].parent.parent}[on]
    mode = stat.S_IMODE(path.stat().st_mode)
    if sys.platform == "darwin":
        grant = "everyone allow read" if on == "file" else "everyone allow search,add_file"
        subprocess.run(["/bin/chmod", "+a", grant, path], check=True, capture_output=True)
    else:
        # Linux POSIX access ACL format: version, then tag/permissions/ID.
        # A zero mask keeps the Unix group mode zero even with a named grant.
        undefined = 0xFFFFFFFF
        named_uid = 65534 if os.geteuid() != 65534 else 65533
        acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in (
            (1, mode >> 6, undefined), (2, 7, named_uid), (4, 0, undefined),
            (16, 0, undefined), (32, 0, undefined),
        ))
        os.setxattr(path, "system.posix_acl_access", acl)
    assert stat.S_IMODE(path.stat().st_mode) == mode
    reject(paths, context)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin extended deny-only ACL")
def test_accepts_darwin_ancestor_with_only_deny_entries(sources):
    _, paths, context = sources
    subprocess.run(["/bin/chmod", "+a", "everyone deny delete", paths[0].parent.parent],
                   check=True, capture_output=True)
    assert compile_current_manifest_from_files(*paths, **context)


def test_rejects_when_effective_acl_cannot_be_read(sources, monkeypatch):
    _, paths, context = sources

    def inaccessible(*args):
        raise OSError(errno.EIO, "synthetic ACL inspection failure")

    if sys.platform == "darwin":
        monkeypatch.setattr(private_source_files, "_darwin_acl_api", inaccessible)
    else:
        monkeypatch.setattr(os, "getxattr", inaccessible)
    reject(paths, context)


@pytest.mark.parametrize("grow", (False, True), ids=("short-read", "bounded-growth"))
def test_rejects_size_drift_at_the_start_of_read(sources, monkeypatch, grow):
    _, paths, context = sources
    target = paths[0]
    before = target.stat()
    read = os.read
    received = []

    def read_changed_file(fd, count):
        if not os.path.samestat(os.fstat(fd), before):
            return read(fd, count)
        if not received:
            with target.open("r+b") as stream:
                stream.truncate(MAX_APPROVAL_BYTES * 2 if grow else before.st_size // 2)
        raw = read(fd, count)
        received.append(len(raw))
        return raw

    monkeypatch.setattr(os, "read", read_changed_file)
    reject(paths, context)
    assert sum(received) == (before.st_size + 1 if grow else before.st_size // 2)


@pytest.mark.parametrize("change", ("replace", "restore-bytes", "change-mode", "rename-ancestor"))
def test_rejects_sources_changed_while_reading_later_input(sources, monkeypatch, change):
    _, paths, context = sources
    read_exact = private_source_files._read_exact
    target = paths[0]

    def change_earlier_source(source):
        raw = read_exact(source)
        if source.name == paths[-1].name:
            if change == "replace":
                # Leave the original inode linked and install an exact clone.
                original = target.rename(target.with_suffix(".orig"))
                target.write_bytes(original.read_bytes())
                target.chmod(0o600)
            elif change == "restore-bytes":
                before = target.stat()
                original = target.read_bytes()
                target.write_bytes(b"!" * len(original))
                target.write_bytes(original)
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
            elif change == "change-mode":
                target.chmod(0o644)
            else:
                ancestor = target.parent.parent
                ancestor.rename(ancestor.with_suffix(".moved"))
        return raw

    monkeypatch.setattr(private_source_files, "_read_exact", change_earlier_source)
    reject(paths, context)
