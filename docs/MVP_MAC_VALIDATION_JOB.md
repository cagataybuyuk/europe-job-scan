# Independent Mac validation job foundation

This is a one-shot **validation-only** launchd job generator and a read-only
packaging/input doctor. It can run from Terminal without Codex when the reviewed
Python package is installed in its explicit venv. It is not an independent live
application MVP: no browser, canonical database, field write, CV upload, Next,
final submission, retry, transport or permission handoff is enabled here.

## Fixed command and explicit configuration

Installed console entry: `ejs-mac-validation`. Equivalent module invocation:

```text
/absolute/reviewed/venv/bin/python -I -B -m ejs.apps.mac_validation --config /absolute/private/config.json --config-sha256 EXTERNALLY_REVIEWED_SHA256
```

The default action checks readiness and writes no files. Add only
`--generate-validation-job` to explicitly produce a private, unloaded plist.
There is no install/load/bootstrap/enable/start operation in this command. No
service, schedule or background process is installed by generating the file.

The owner-only (0600) config in an owner-only (0700) directory must have exactly
these keys. All paths are absolute and every hash is separately reviewed:

```json
{
  "schema_version": "EJS-MAC-VALIDATION-JOB-1",
  "python_path": "/absolute/reviewed/venv/bin/python",
  "python_sha256": "ACTUAL_RESOLVED_INTERPRETER_SHA256",
  "venv_path": "/absolute/reviewed/venv",
  "venv_config_sha256": "ACTUAL_PYVENV_CFG_SHA256",
  "repo_path": "/absolute/reviewed/source",
  "source_sha": "REVIEWED_CLEAN_SOURCE_COMMIT",
  "manifest_path": "/absolute/private/manifest.json",
  "manifest_sha256": "EXTERNALLY_REVIEWED_MANIFEST_SHA256",
  "facts_path": "/absolute/private/facts.json",
  "facts_sha256": "EXTERNALLY_REVIEWED_FACTS_SHA256",
  "cv_path": "/absolute/private/CV.pdf",
  "job_directory": "/absolute/private/validation-job"
}
```

Placeholders above are explanatory and do not pass validation. The output
directory must already be private; it must not be a LaunchAgents/LaunchDaemons
directory. No environment credentials, arbitrary module/arguments, grant,
execution switch, URL or interval is accepted in the config.

## Readiness and interpreter binding

Before generation, the doctor checks the actual private input compiler, approved
CV bytes, exact Devoteam target, native-entry policy and clean source commit.
Private config/manifest/facts/CV/output paths retain strict no-symlink rules.

A narrowly checked **standard venv interpreter** symlink chain is the sole
exception. The real venv and bin directories must be owned and not writable by
others. Its regular `pyvenv.cfg` bytes and the actual executable bytes are pinned.
Link chains are bounded; executable/link ownership is limited to the current
account or root, unsafe writable locations/targets and loops reject. The venv
executable path stays in ProgramArguments so venv site-packages are preserved.

The selected interpreter is probed in isolated mode. Its `sys.prefix` must match
the explicit venv and its installed reviewed-application module must resolve to
the exact reviewed source checkout. Running from a different working directory
does not repair a stale editable install under `-I`. In particular, the older
gd005-router venv pointing to gd005-router source is not assumed to be the
canonical runtime. The doctor does not install or repair dependencies.

## Generated job and logs

The fixed label/file is `com.ejs.test.validation` / `com.ejs.test.validation.plist`.
ProgramArguments invokes only:

```text
<reviewed-venv-python> -I -B -m ejs.apps.reviewed_application --manifest <pinned-path> --facts <pinned-path> --manifest-sha256 <pin> --facts-sha256 <pin> --source-sha <pin> --cv <approved-path>
```

There is no execute/grant flag. `RunAtLoad=false`, `KeepAlive=false` and no
interval, calendar, watch, socket or restart trigger is emitted. Generating or
linting the plist is not evidence that launchd ran it. This foundation does not
retry failures or claim any new application authority.

The plist is private 0600, logs directory 0700, validation log 0600 and job umask
077. Standard output records only the existing CLI's sanitized validation JSON;
raw startup stderr is discarded to `/dev/null`. No private field values, codes,
cookies or credentials are copied into a config or log. Identical generation
preserves prior logs; conflicting or unsafe output files are not overwritten.

A later separately reviewed per-user launchd setup requires the Mac user to be
logged in and the Mac to be available/awake. Logout ends per-user jobs. No such
setup or actual background run is performed by this implementation. An eventual
independent live MVP still needs its existing reviewed scope, actual form and
endpoint evidence, durable history, confirmation and central-publication gates.

## Verification

`tests/test_mac_validation.py` uses the real private compiler/CV/default command
composition and actual plistlib serialization. It lints a temporary plist with
`plutil` when available. Packaging/source/import drift, unsafe input paths,
interpreter/metadata drift, execute/module/environment injection and output
conflicts stop before generation. All fixtures are synthetic; no canonical DB,
employer, launchctl or host-service operation is used.

References: [Apple launchd job documentation](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
and [Python plistlib documentation](https://docs.python.org/3.12/library/plistlib.html).
