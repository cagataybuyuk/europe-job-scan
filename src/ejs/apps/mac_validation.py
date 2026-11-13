"""Read-only Mac doctor and explicit one-shot launchd validation plist generator.

No install/load/bootstrap/enable action, schedule, browser or database execution
is implemented. Generated arguments can invoke only the existing default CLI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess

from ejs.apps.reviewed_application import __main__ as application
from ejs.services.application_outcome import _publish
from ejs.services.oneclick_target import OneClickTarget, native_entry_policy_enabled
from ejs.services.single_application_inputs import load_reviewed_application_inputs
from ejs.services.single_application_runtime import read_approved_cv


_LABEL = "com.ejs.test.validation"
_FILENAME = _LABEL + ".plist"
_ERROR = "MAC_VALIDATION_REVIEW_REQUIRED"
_KEYS = frozenset({"schema_version", "python_path", "python_sha256", "venv_path",
    "venv_config_sha256", "repo_path", "source_sha", "manifest_path", "manifest_sha256",
    "facts_path", "facts_sha256", "cv_path", "job_directory"})
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SOURCE = re.compile(r"[a-f0-9]{40}\Z")
_ENV = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
_PROBE = ("import importlib.util,json,sys;"
    "s=importlib.util.find_spec('ejs.apps.reviewed_application.__main__');"
    "print(json.dumps({'prefix':sys.prefix,'origin':s.origin if s else None}))")


def _require(ok):
    if not ok:
        raise PermissionError(_ERROR)


def _now():
    return datetime.now(timezone.utc)


def _repo():
    return Path(__file__).resolve().parents[3]


def _path(value) -> Path:
    _require(type(value) is str and 0 < len(value) <= 1024
             and all(ord(c) >= 32 and ord(c) != 127 for c in value))
    path = Path(value)
    _require(path.is_absolute() and ".." not in path.parts)
    return path


def _real_directory(path, *, private=False):
    for component in (path, *path.parents):
        info = component.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))
    info = path.lstat()
    _require(info.st_uid == os.getuid()
             and (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022))


def _trusted_bytes(path, pin, *, max_bytes):
    """Reviewed interpreter/venv metadata may be readable, never writable by others."""
    _require(type(pin) is str and _HASH.fullmatch(pin) is not None)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_uid in (0, os.getuid())
                 and not before.st_mode & 0o022 and 0 < before.st_size <= max_bytes)
        digest = hashlib.sha256()
        content = bytearray() if max_bytes <= 65536 else None
        total = 0
        while True:
            part = os.read(fd, min(65536, max_bytes + 1 - total))
            if not part:
                break
            total += len(part)
            _require(total <= max_bytes)
            digest.update(part)
            if content is not None:
                content.extend(part)
        after = os.fstat(fd)
        _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                 == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                 and digest.hexdigest() == pin)
        return bytes(content) if content is not None else None
    finally:
        os.close(fd)


def _interpreter(config):
    venv, python = _path(config["venv_path"]), _path(config["python_path"])
    _real_directory(venv)
    _real_directory(venv / "bin")
    _require(python.parent == venv / "bin" and re.fullmatch(r"python(?:3(?:\.[0-9]{1,2})?)?", python.name))
    metadata = _trusted_bytes(venv / "pyvenv.cfg", config["venv_config_sha256"], max_bytes=65536)
    settings = dict(line.split("=", 1) for line in metadata.decode("utf-8").splitlines() if "=" in line)
    settings = {k.strip(): v.strip() for k, v in settings.items()}
    _require(settings.get("include-system-site-packages", "").lower() == "false")
    # Standard venv and Homebrew links are the sole symlink exception. Resolve
    # each link/component to an absolute path and check ownership/mode; private
    # config/input/output paths never use this exception.
    pending = list(python.parts[1:])
    current, links = Path(python.anchor), 0
    while pending:
        current = current / pending.pop(0)
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            links += 1
            _require(links <= 16 and info.st_uid in (0, os.getuid()))
            destination = current.readlink()
            destination = destination if destination.is_absolute() else current.parent / destination
            destination = Path(os.path.normpath(destination))
            _require(destination.is_absolute())
            pending = list(destination.parts[1:]) + pending
            current = Path(destination.anchor)
        elif pending:
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid())
                     and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
        else:
            _require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.getuid())
                     and not info.st_mode & 0o022 and info.st_mode & 0o111)
    _trusted_bytes(current, config["python_sha256"], max_bytes=128 * 1024 * 1024)
    return python, venv


def _config(path, pin):
    data = application.read_private_pinned_bytes(path, pin)
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    config = json.loads(data, object_pairs_hook=pairs)
    _require(type(config) is dict and set(config) == _KEYS
             and config["schema_version"] == "EJS-MAC-VALIDATION-JOB-1"
             and type(config["source_sha"]) is str and _SOURCE.fullmatch(config["source_sha"]) is not None)
    for key in ("manifest_sha256", "facts_sha256", "python_sha256", "venv_config_sha256"):
        _require(type(config[key]) is str and _HASH.fullmatch(config[key]) is not None)
    for key in ("python_path", "venv_path", "repo_path", "manifest_path", "facts_path", "cv_path", "job_directory"):
        _path(config[key])
    return config


def validation_arguments(config):
    """No caller-controlled module/options: only existing default validation flags."""
    return [config["python_path"], "-I", "-B", "-m", "ejs.apps.reviewed_application",
        "--manifest", config["manifest_path"], "--facts", config["facts_path"],
        "--manifest-sha256", config["manifest_sha256"], "--facts-sha256", config["facts_sha256"],
        "--source-sha", config["source_sha"], "--cv", config["cv_path"]]


def doctor(config_path: Path, config_sha256: str) -> dict:
    """Validate packaging and exact private inputs without creating any outputs."""
    try:
        config = _config(config_path, config_sha256)
        repo, output = _path(config["repo_path"]), _path(config["job_directory"])
        _require(repo == _repo())
        _real_directory(repo)
        _real_directory(output, private=True)
        _require(not {"LaunchAgents", "LaunchDaemons"}.intersection(output.parts))
        source = subprocess.run(["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10, env=_ENV).stdout.strip()
        dirty = subprocess.run(["/usr/bin/git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            check=True, capture_output=True, text=True, timeout=10, env=_ENV).stdout
        _require(source == config["source_sha"] and not dirty)
        manifest, _facts = load_reviewed_application_inputs(_path(config["manifest_path"]), _path(config["facts_path"]),
            manifest_sha256=config["manifest_sha256"], facts_sha256=config["facts_sha256"],
            source_sha=source, now=_now())
        _require(OneClickTarget(**manifest["target"]) == application._TARGET and native_entry_policy_enabled(manifest))
        read_approved_cv(_path(config["cv_path"]), manifest["cv"])
        python, venv = _interpreter(config)
        probe = subprocess.run([str(python), "-I", "-B", "-c", _PROBE],
            check=True, capture_output=True, text=True, timeout=10, env=_ENV, cwd=repo)
        _require(len(probe.stdout) <= 8192)
        packaging = json.loads(probe.stdout)
        _require(packaging == {"prefix": str(venv),
            "origin": str(repo / "src/ejs/apps/reviewed_application/__main__.py")})
        return {"config": config, "status": "VALIDATION_JOB_READY", "browser_action_performed": False,
                "database_action_performed": False, "submit_authorized": False}
    except Exception:
        raise PermissionError(_ERROR) from None


def _log(path):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        _require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    else:
        _publish(path, b'{"status":"NOT_RUN","submit_authorized":false}\n')


def generate_validation_job(config_path: Path, config_sha256: str) -> dict:
    """Explicit file generation only; launchctl and service installation are absent."""
    try:
        checked = doctor(config_path, config_sha256)
        config = checked["config"]
        output = _path(config["job_directory"])
        logs = output / "logs"
        try:
            logs.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _real_directory(logs, private=True)
        _log(logs / "validation.jsonl")
        payload = {"Label": _LABEL, "ProgramArguments": validation_arguments(config),
            "WorkingDirectory": config["repo_path"], "RunAtLoad": False, "KeepAlive": False,
            "StandardOutPath": str(logs / "validation.jsonl"), "StandardErrorPath": "/dev/null",
            "Umask": 0o077}
        plist = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)
        _require(plistlib.loads(plist) == payload and len(plist) <= 8192)
        wrote = _publish(output / _FILENAME, plist)
        return {"status": "VALIDATION_JOB_GENERATED", "plist_write_performed": wrote,
            "service_installed": False, "service_loaded": False, "background_job_started": False,
            "browser_action_performed": False, "database_action_performed": False, "submit_authorized": False}
    except Exception:
        raise PermissionError(_ERROR) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only EJS Mac validation doctor; explicitly generate an unloaded one-shot validation plist.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--generate-validation-job", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = (generate_validation_job(args.config, args.config_sha256) if args.generate_validation_job
                  else {k: v for k, v in doctor(args.config, args.config_sha256).items() if k != "config"})
        code = 0
    except (Exception, KeyboardInterrupt):
        result = {"status": "BLOCKED", "error_code": _ERROR, "browser_action_performed": False,
                  "database_action_performed": False, "submit_authorized": False}
        code = 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
