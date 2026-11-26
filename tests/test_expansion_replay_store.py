"""Private W3.5 persistence with synthetic inputs and no transport."""
import fcntl
import json
import os
import stat

import pytest

from scripts import expansion_replay_store as store
from tests.test_expansion_replay import TENANT, page


@pytest.fixture
def directory(tmp_path):
    return tmp_path.resolve() / "offline"


def start(directory):
    return store.replay_private_batch(directory, {
        (TENANT, "NL", 0): page("NL", total=2, ids=("a",)),
    }, initialize=True, max_pages=1)


def resume(directory):
    return store.replay_private_batch(directory, {
        (TENANT, "NL", 1): page("NL", offset=1, total=2, ids=("b",)),
        (TENANT, "IE", 0): page("IE", total=1, ids=("a",)),
    }, max_pages=2)


def test_resume_commits_cursor_and_deduplicated_output_together(directory):
    first = start(directory)
    second = resume(directory)
    bundle_path = directory / store.BUNDLE_NAME
    bundle = json.loads(bundle_path.read_bytes())
    assert first.checkpoint.cursors[0].offset == 1
    assert second.checkpoint.cursors[0].status == "complete"
    assert second.checkpoint.cursors[1].status == "complete"
    assert [row["posting_id"] for row in second.listings] == ["b"]
    assert second.duplicates == 1
    assert [row["posting_id"] for row in bundle["listings"]] == ["a", "b"]
    assert bundle["checkpoint"]["cursors"][0]["seen"] == ["a", "b"]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(bundle_path.stat().st_mode) == 0o600


def test_explicit_initialization_never_overwrites(directory):
    with pytest.raises(PermissionError, match=store.STORE_ERROR):
        resume(directory)
    assert not directory.exists()
    start(directory)
    path = directory / store.BUNDLE_NAME
    before = path.read_bytes()
    with pytest.raises(PermissionError, match=store.STORE_ERROR):
        start(directory)
    assert path.read_bytes() == before


@pytest.mark.parametrize("tamper", ["removed_listing", "duplicate_listing", "wrong_url",
                                    "wrong_country", "duplicate_json_key", "forged_cursor",
                                    "invalid_json", "too_large"])
def test_inconsistent_or_malformed_bundle_fails_closed(directory, tamper, monkeypatch):
    start(directory)
    path = directory / store.BUNDLE_NAME
    bundle = json.loads(path.read_bytes())
    if tamper == "removed_listing":
        bundle["listings"].clear()
    elif tamper == "duplicate_listing":
        bundle["listings"].append(bundle["listings"][0])
    elif tamper == "wrong_url":
        bundle["listings"][0]["url"] += "?private=sentinel"
    elif tamper == "wrong_country":
        bundle["listings"][0]["location"] = "Synthetic city, DE"
    elif tamper == "forged_cursor":
        bundle["checkpoint"]["cursors"][0]["seen"] = ["unpublished"]
    raw = json.dumps(bundle).encode()
    if tamper == "duplicate_json_key":
        raw = b'{"schema":"ignored",' + raw[1:]
    elif tamper == "invalid_json":
        raw = b'{"private":"synthetic sentinel",}'
    elif tamper == "too_large":
        monkeypatch.setattr(store, "_MAX_BYTES", len(raw) - 1)
    path.write_bytes(raw)
    with pytest.raises(PermissionError, match=store.STORE_ERROR) as error:
        resume(directory)
    assert error.value.__context__ is None
    assert path.read_bytes() == raw


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "world_readable", "fifo"])
def test_unsafe_bundle_is_refused(directory, kind):
    start(directory)
    path = directory / store.BUNDLE_NAME
    outside = directory.parent / "sentinel"
    path.rename(outside)
    original = outside.read_bytes()
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    elif kind == "world_readable":
        path.write_bytes(original)
        path.chmod(0o644)
    else:
        os.mkfifo(path, 0o600)
    with pytest.raises(PermissionError, match=store.STORE_ERROR):
        resume(directory)
    assert outside.read_bytes() == original


@pytest.mark.parametrize("kind", ["repo", "control", "symlink", "open_mode"])
def test_unsafe_destination_is_refused(directory, kind):
    if kind in {"repo", "control"}:
        directory.parent.joinpath("control").mkdir(mode=0o700)
        if kind == "repo":
            directory.parent.joinpath(".git").write_text("gitdir: /missing")
        else:
            directory = directory.parent / "control" / "offline"
    elif kind == "symlink":
        real = directory.parent / "real"
        real.mkdir(mode=0o700)
        directory.symlink_to(real, target_is_directory=True)
    else:
        directory.mkdir(mode=0o755)
    with pytest.raises(PermissionError, match=store.STORE_ERROR):
        start(directory)
    assert not (directory / store.BUNDLE_NAME).exists()


def test_existing_process_lock_cannot_be_bypassed(directory):
    start(directory)
    with (directory / ".expansion-replay.lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(PermissionError, match=store.STORE_ERROR):
            resume(directory)
    assert resume(directory).pages == 2


def test_failed_atomic_replace_keeps_prior_bundle(directory, monkeypatch):
    start(directory)
    path = directory / store.BUNDLE_NAME
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("synthetic write error")

    with monkeypatch.context() as patch:
        patch.setattr(store.os, "replace", fail)
        with pytest.raises(PermissionError, match=store.STORE_ERROR):
            resume(directory)
    assert path.read_bytes() == before
    assert not list(directory.glob("*.tmp"))
    assert [row["posting_id"] for row in resume(directory).listings] == ["b"]


def test_resume_in_new_process_only_persists_normalized_output(directory):
    import subprocess
    import sys

    payload = page("NL", total=2, ids=("a",))
    payload["content"][0].update(applyUrl="https://example.invalid/?secret=sentinel",
                                 candidate_note="sentinel")
    store.replay_private_batch(directory, {(TENANT, "NL", 0): payload},
                               initialize=True, max_pages=1)
    subprocess.run([sys.executable, "-c", """
from pathlib import Path
import sys
from tests.test_expansion_replay_store import resume

result = resume(Path(sys.argv[1]))
assert result.duplicates == 1
assert [row["posting_id"] for row in result.listings] == ["b"]
""", str(directory)], check=True, timeout=10)
    raw = (directory / store.BUNDLE_NAME).read_bytes()
    assert b"sentinel" not in raw
    assert b"example.invalid" not in raw
    assert b"https://jobs.smartrecruiters.com/KPMGNederland/a" in raw
