"""Real private compiler/plist tests; no canonical state or launchd service."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
from types import SimpleNamespace

import pytest

from ejs.apps import mac_validation as app
from ejs.apps.reviewed_application import __main__ as application
from tests.test_single_application_inputs import inputs


_REAL_RUN = subprocess.run
NOW = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)


def raw(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def private_file(path, payload):
    path.write_bytes(payload)
    path.chmod(0o600)
    return sha(payload)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    private, output, repo, venv = [base / name for name in ("inputs", "job", "repo", "venv")]
    for path in (private, output, repo, venv):
        path.mkdir(mode=0o700)
    (venv / "bin").mkdir(mode=0o700)
    python = venv / "bin/python"
    python.write_bytes(b"SYNTHETIC REVIEWED EXECUTABLE FIXTURE")
    python.chmod(0o700)
    cfg = venv / "pyvenv.cfg"
    cfg.write_bytes(b"include-system-site-packages = false\nversion = 3.12.16\n")
    cfg.chmod(0o600)
    cv = private / "CV.pdf"
    cv_hash = private_file(cv, b"%PDF-1.4\n% SYNTHETIC ONLY\n%%EOF\n")
    manifest, facts = inputs()
    manifest.update(source_sha="a" * 40, reviewed_at=NOW.isoformat(), target=asdict(application._TARGET),
        entry_policy=dict(strategy="native_verified_facts", decision_ref="USER-NATIVE-MVP-20261112"))
    manifest["steps"][0]["url"] = application._TARGET.entry_url
    manifest["cv"].update(sha256=cv_hash, size_bytes=cv.stat().st_size, approved_filename="CV.pdf")
    for fact in facts["facts"]:
        fact["verified_at"] = NOW.isoformat()
    mp, fp = private / "manifest.json", private / "facts.json"
    md, fd = private_file(mp, raw(manifest)), private_file(fp, raw(facts))
    config = dict(schema_version="EJS-MAC-VALIDATION-JOB-1", python_path=str(python),
        python_sha256=sha(python.read_bytes()), venv_path=str(venv), venv_config_sha256=sha(cfg.read_bytes()),
        repo_path=str(repo), source_sha="a" * 40, manifest_path=str(mp), manifest_sha256=md,
        facts_path=str(fp), facts_sha256=fd, cv_path=str(cv), job_directory=str(output))
    cp = private / "config.json"
    pin = private_file(cp, raw(config))
    state = SimpleNamespace(config=config, config_path=cp, pin=pin, private=private, output=output,
        repo=repo, venv=venv, python=python, manifest=manifest, mp=mp, fp=fp, calls=[], dirty=False,
        wrong_source=False, wrong_import=False, wrong_prefix=False)
    def run(command, **kwargs):
        state.calls.append(command)
        assert ("env" not in kwargs or kwargs["env"] == app._ENV) and kwargs["timeout"] == 10
        assert not kwargs.get("shell")
        if command[0] in ("git", "/usr/bin/git"):
            return SimpleNamespace(stdout=("dirty" if state.dirty else "") if "status" in command
                                   else "b" * 40 if state.wrong_source else "a" * 40)
        assert command == [str(python), "-I", "-B", "-c", app._PROBE]
        return SimpleNamespace(stdout=json.dumps({"prefix": str(base / "other") if state.wrong_prefix else str(venv),
            "origin": str(base / "old/src/ejs/apps/reviewed_application/__main__.py") if state.wrong_import
                      else str(repo / "src/ejs/apps/reviewed_application/__main__.py")}))
    monkeypatch.setattr(app.subprocess, "run", run)
    monkeypatch.setattr(app, "_repo", lambda: repo)
    monkeypatch.setattr(app, "_now", lambda: NOW)
    monkeypatch.setattr(application, "_now", lambda: NOW)
    monkeypatch.setattr(application, "open_existing_canonical_ledger",
                        lambda: (_ for _ in ()).throw(AssertionError("DB MUST NOT OPEN")))
    return state


def repin(fixture):
    fixture.pin = private_file(fixture.config_path, raw(fixture.config))


def blocked(fn):
    with pytest.raises(PermissionError, match="^MAC_VALIDATION_REVIEW_REQUIRED$"):
        fn()


def test_default_doctor_actual_compiler_cv_and_packaging_has_no_outputs(fixture, capsys):
    assert app.main(["--config", str(fixture.config_path), "--config-sha256", fixture.pin]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == dict(status="VALIDATION_JOB_READY", browser_action_performed=False,
                         database_action_performed=False, submit_authorized=False)
    assert list(fixture.output.iterdir()) == []
    assert all("launchctl" not in str(command) for command in fixture.calls)


def test_plist_is_one_shot_private_unloaded_and_lints_if_plutil_exists(fixture):
    result = app.generate_validation_job(fixture.config_path, fixture.pin)
    assert result["status"] == "VALIDATION_JOB_GENERATED" and result["plist_write_performed"] is True
    assert result["service_installed"] is result["service_loaded"] is result["background_job_started"] is False
    path = fixture.output / "com.ejs.test.validation.plist"
    payload = plistlib.loads(path.read_bytes())
    assert payload["Label"] == "com.ejs.test.validation"
    assert payload["RunAtLoad"] is payload["KeepAlive"] is False
    assert payload["ProgramArguments"] == app.validation_arguments(fixture.config)
    assert set(payload) == {"Label", "ProgramArguments", "WorkingDirectory", "RunAtLoad", "KeepAlive",
                           "StandardOutPath", "StandardErrorPath", "Umask"}
    assert payload["StandardErrorPath"] == "/dev/null" and payload["Umask"] == 0o077
    assert path.stat().st_mode & 0o777 == 0o600
    logs = fixture.output / "logs"
    assert logs.stat().st_mode & 0o777 == 0o700
    assert (logs / "validation.jsonl").stat().st_mode & 0o777 == 0o600
    plutil = Path("/usr/bin/plutil")
    if plutil.is_file():
        result = _REAL_RUN([str(plutil), "-lint", str(path)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0


def test_generated_arguments_invoke_real_existing_default_validation_cli(fixture, capsys):
    config = app.doctor(fixture.config_path, fixture.pin)["config"]
    args = app.validation_arguments(config)
    assert args[:5] == [str(fixture.python), "-I", "-B", "-m", "ejs.apps.reviewed_application"]
    assert "--execute-reviewed-application" not in args and "--grant" not in args
    # Real standard input compiler and existing application's default branch;
    # its canonical DB entry point raises if anything accidentally calls it.
    assert application.main(args[5:]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INPUTS_VALIDATED" and result["browser_action_performed"] is False


@pytest.mark.parametrize("key,value", [("execute", True), ("grant", "/fixture/grant.json"),
    ("module", "os"), ("ProgramArguments", ["/bin/sh", "-c", "bad"]),
    ("environment", {"API_KEY": "SYNTHETIC PRIVATE"}), ("interval", 60), ("RunAtLoad", True)])
def test_unreviewed_execute_shell_module_credentials_or_schedule_fields_reject(fixture, key, value):
    fixture.config[key] = value
    repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir()) and fixture.calls == []


@pytest.mark.parametrize("bad", ["dirty", "wrong_source", "wrong_import", "wrong_prefix"])
def test_source_or_installed_venv_import_drift_blocks_generation(fixture, bad):
    setattr(fixture, bad, True)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())


@pytest.mark.parametrize("bad", ["config_pin", "manifest_pin", "facts_pin", "cv_bytes", "target", "native"])
def test_changed_private_inputs_fail_before_generating(fixture, bad):
    if bad == "config_pin":
        fixture.pin = "f" * 64
    elif bad == "manifest_pin":
        fixture.config["manifest_sha256"] = "f" * 64
    elif bad == "facts_pin":
        fixture.config["facts_sha256"] = "f" * 64
    elif bad == "cv_bytes":
        Path(fixture.config["cv_path"]).write_bytes(b"%PDF-CHANGED")
    else:
        if bad == "target":
            fixture.manifest["target"]["company_id"] = "a" * 24
        else:
            del fixture.manifest["entry_policy"]
        fixture.config["manifest_sha256"] = private_file(fixture.mp, raw(fixture.manifest))
    if bad != "config_pin":
        repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())


@pytest.mark.parametrize("which", ["config", "manifest", "facts", "cv", "output"])
def test_private_input_or_output_symlink_is_never_followed(fixture, which):
    if which == "config":
        alias = fixture.private / "alias.json"
        alias.symlink_to(fixture.config_path)
        fixture.config_path = alias
    elif which == "output":
        alias = fixture.output.parent / "alias-output"
        alias.symlink_to(fixture.output, target_is_directory=True)
        fixture.config["job_directory"] = str(alias)
        repin(fixture)
    else:
        key = which + "_path"
        original = Path(fixture.config[key])
        alias = fixture.private / ("alias-" + original.name)
        alias.symlink_to(original)
        fixture.config[key] = str(alias)
        repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())


def test_standard_verified_venv_symlink_preserves_venv_argv_path(fixture):
    actual = fixture.venv / "bin/python3.12"
    fixture.python.rename(actual)
    fixture.python.symlink_to(actual.name)
    result = app.doctor(fixture.config_path, fixture.pin)
    assert result["status"] == "VALIDATION_JOB_READY"
    assert app.validation_arguments(fixture.config)[0] == str(fixture.python)


@pytest.mark.parametrize("bad", ["loop", "writable_binary", "wrong_binary_hash", "wrong_metadata_hash", "system_site"])
def test_unsafe_interpreter_or_venv_metadata_rejects(fixture, bad):
    if bad == "loop":
        fixture.python.unlink()
        fixture.python.symlink_to(fixture.python.name)
    elif bad == "writable_binary":
        fixture.python.chmod(0o777)
    elif bad == "wrong_binary_hash":
        fixture.config["python_sha256"] = "f" * 64
        repin(fixture)
    elif bad == "wrong_metadata_hash":
        fixture.config["venv_config_sha256"] = "f" * 64
        repin(fixture)
    else:
        metadata = fixture.venv / "pyvenv.cfg"
        metadata.write_bytes(b"include-system-site-packages = true\n")
        fixture.config["venv_config_sha256"] = sha(metadata.read_bytes())
        repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())


def test_idempotent_generation_preserves_logs_and_conflicting_job_is_not_replaced(fixture):
    assert app.generate_validation_job(fixture.config_path, fixture.pin)["plist_write_performed"]
    log = fixture.output / "logs/validation.jsonl"
    log.write_bytes(b'{"status":"INPUTS_VALIDATED"}\n')
    assert not app.generate_validation_job(fixture.config_path, fixture.pin)["plist_write_performed"]
    assert log.read_bytes() == b'{"status":"INPUTS_VALIDATED"}\n'
    plist = fixture.output / "com.ejs.test.validation.plist"
    plist.write_bytes(b"existing conflicting private bytes")
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert plist.read_bytes() == b"existing conflicting private bytes"


def test_no_service_install_directory_or_control_character_path(fixture):
    for directory in (str(fixture.output / "LaunchAgents"), str(fixture.output) + "\n--execute"):
        fixture.config["job_directory"] = directory
        repin(fixture)
        blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))


def test_blocked_doctor_stdout_does_not_expose_private_paths_or_values(fixture, capsys):
    fixture.config["API_KEY"] = "SYNTHETIC PRIVATE"
    repin(fixture)
    assert app.main(["--config", str(fixture.config_path), "--config-sha256", fixture.pin]) == 2
    stdout = capsys.readouterr().out
    assert "SYNTHETIC" not in stdout and str(fixture.private) not in stdout
    assert json.loads(stdout)["status"] == "BLOCKED"


@pytest.mark.parametrize("component", ["config_parent", "output_parent"])
def test_symlinked_parent_directory_rejects_before_any_job_write(fixture, component):
    """A private leaf is insufficient when a parent redirects the job."""
    if component == "config_parent":
        alias = fixture.private.parent / "redirected-inputs"
        alias.symlink_to(fixture.private, target_is_directory=True)
        fixture.config_path = alias / "config.json"
    else:
        alias = fixture.output.parent / "redirected-job"
        alias.symlink_to(fixture.output, target_is_directory=True)
        fixture.config["job_directory"] = str(alias)
        repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())


def test_validation_arguments_remain_fixed_and_never_enable_execution(fixture):
    args = app.validation_arguments(fixture.config)
    assert args.count("-m") == 1
    assert args[args.index("-m") + 1] == "ejs.apps.reviewed_application"
    assert not any(token in args for token in ("--execute-reviewed-application", "--grant",
        "--submit", "--target-url", "-c", "-W", "--interval"))
    assert args.count("--manifest") == args.count("--facts") == args.count("--cv") == 1


@pytest.mark.parametrize("bad", ["symlink_log", "world_writable_log", "hardlinked_log"])
def test_untrusted_existing_log_blocks_without_replacing_plist(fixture, bad):
    """Preexisting log paths must not be followed or silently reused."""
    logs = fixture.output / "logs"
    logs.mkdir(mode=0o700)
    log = logs / "validation.jsonl"
    if bad == "symlink_log":
        outside = fixture.private / "outside.jsonl"
        outside.write_bytes(b"SENTINEL")
        log.symlink_to(outside)
    else:
        log.write_bytes(b"SENTINEL")
        log.chmod(0o666 if bad == "world_writable_log" else 0o600)
        if bad == "hardlinked_log":
            os.link(log, fixture.private / "other-log.jsonl")
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not (fixture.output / "com.ejs.test.validation.plist").exists()
    if bad == "symlink_log":
        assert outside.read_bytes() == b"SENTINEL"
    else:
        assert log.read_bytes() == b"SENTINEL"


@pytest.mark.parametrize("bad", ["interpreter_parent_symlink", "interpreter_parent_writable",
                                  "venv_config_symlink", "unexpected_python_name"])
def test_interpreter_path_and_metadata_fail_closed_before_job_write(fixture, bad):
    """No symlinked/writable interpreter parent, redirected metadata or unexpected executable."""
    if bad == "interpreter_parent_symlink":
        real_bin = fixture.venv / "real-bin"
        (fixture.venv / "bin").rename(real_bin)
        (fixture.venv / "bin").symlink_to(real_bin, target_is_directory=True)
    elif bad == "interpreter_parent_writable":
        (fixture.venv / "bin").chmod(0o777)
    elif bad == "venv_config_symlink":
        cfg = fixture.venv / "pyvenv.cfg"
        actual = fixture.venv / "metadata-real"
        cfg.rename(actual)
        cfg.symlink_to(actual)
    else:
        fixture.config["python_path"] = str(fixture.venv / "bin/python-unreviewed")
        repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())
    assert not any("launchctl" in str(call) for call in fixture.calls)


@pytest.mark.parametrize("key,value", [
    ("StartInterval", 60), ("StartCalendarInterval", {"Minute": 0}),
    ("KeepAlive", True), ("WatchPaths", ["/tmp"]),
    ("QueueDirectories", ["/tmp"]), ("MachServices", {"com.ejs.test": True}),
])
def test_unreviewed_launchd_triggers_fail_closed_without_job_mutation(fixture, key, value):
    """Reject launchd persistence/activation keys before generating any job artifact."""
    fixture.config[key] = value
    repin(fixture)
    blocked(lambda: app.generate_validation_job(fixture.config_path, fixture.pin))
    assert not tuple(fixture.output.iterdir())
    assert not any("launchctl" in str(call) for call in fixture.calls)
