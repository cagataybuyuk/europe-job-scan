# Engine queue, results and runtime switch (GD-006 step 5)

The engine runs on the owner's Mac (launchd, step 6) with
`python -m ejs.apps.policy_engine`. Everything lives in owner-only folders under
`~/Local/Projeler/EJS/` (folders 0700, files 0600, no symlinks):

| Path | Who writes | Content |
|---|---|---|
| `engine-queue/inbox/*.json` | Claude's daily task | EJS-EXECUTION-MANIFEST-1 (one per application) |
| `engine-queue/processing/` | engine | claimed manifest (atomic rename) |
| `engine-queue/outbox/<execution_id>.result.json` | engine | EJS-ENGINE-RESULT-1 |
| `engine-queue/archive/` | engine | processed manifests |
| `engine-queue/control/runtime.json` | owner | `{"schema_version": "EJS-ENGINE-RUNTIME-1", "final_submit": true}` enables submission |
| `engine-queue/control/STOP` | owner | any file here stops all submissions (kill switch) |
| `claude-applications/<cv file>` | owner | approved CV PDFs named in the manifests |
| `.ejs-local/first-application.sqlite3` | engine | canonical ledger (existing; v3 policy table added on first run) |

Safe by default: without a valid `runtime.json`, or with `STOP` present, no form is
opened (`RUNTIME_DISABLED` / `KILL_SWITCH`). `STOP` and `runtime.json` are re-read for every item and at
every point of use inside an item (`runtime_guard`, mandatory `guard` of `PolicyApplicationRuntime`): before the
browser opens, before every field write, the CV upload and every page advance, after an owner challenge wait,
before `mark_started` and immediately before the final dispatch. A missing, malformed, unsafe or unreadable
control state denies. A denial before `mark_started` releases the reservation (`BLOCKED`,
`POLICY_APP_RUNTIME_AUTH`); after an owner handoff the key stays held for the owner; a denial after
`mark_started` leaves `SUBMITTED_UNCONFIRMED` (no click, never retried). This is not the independent
approval check (pending) and does not change who may write `control/`.

The runtime binds the same check (plus manifest expiry at the current UTC time) to the browser's network
boundary (`bind_guard`). The SmartRecruiters boundary asks again when it decides a CV or application POST
and immediately before the route forwards any write; an unbound boundary denies every POST. A `STOP`
lookup that fails for any reason other than ENOENT counts as `STOP` present. Manifest expiry is rechecked
before every guarded point, after an owner wait (the key stays held for the owner) and before every POST.

## Result file (EJS-ENGINE-RESULT-1)

`schema_version`, `execution_id`, `manifest_sha256`, `status`, `code`,
`submit_clicks` (1, 0, or -1 when the ledger row is started but the outcome is unverified), `evidence_ref`
(hash reference only), `observation` (observe pass: next page `step`, `final`,
`fields` structure without answers), `finished_at`. No candidate values or CV bytes.

Statuses: `EMPLOYER_CONFIRMED`, `SUBMITTED_UNCONFIRMED`, `OBSERVED`, `BLOCKED`,
`KILL_SWITCH`, `RUNTIME_DISABLED`, `MANIFEST_REJECTED`, `DUPLICATE`, `CAP_REACHED`,
`BUSY`, `LEDGER_ERROR`, `INTERRUPTED`, `OWNER_CHECK_REQUIRED` (an interrupted item was in
the owner's hands; check the employer before any retry).

## Rules

- One engine at a time (`engine.lock`). While submission is off, the CLI opens no browser
  and leaves the inbox untouched (`DISABLED`).
- Results are published atomically and never overwrite: a second item with an execution
  id that already has a result is not run (`ENGINE_DUPLICATE_EXECUTION_ID`, written under a
  hash-based name). Archived manifests are never overwritten either.
- A failure while processing one item never stops the rest of the inbox.
- Unsafe or oddly named inbox files are never read or moved (`SKIPPED_UNSAFE`).
- A manifest left in `processing/` by a crash is never re-run: it is reported as
  `INTERRUPTED`, or `SUBMITTED_UNCONFIRMED`/`EMPLOYER_CONFIRMED` if the ledger shows a
  dispatch, then archived. An unstarted reservation is released; one the owner handled
  is reported as `OWNER_CHECK_REQUIRED`. Claude decides on any new attempt.
- The live result never contradicts the ledger (W36-04). If `mark_started` raises, the
  runtime re-reads the row: only a row still `Reserved` is `BLOCKED` and released. A row
  that shows `Submitted / Unconfirmed` is reported `SUBMITTED_UNCONFIRMED`
  (`POLICY_APP_START_RECORD_ERROR`); an unreadable or unexpected state is reported
  `SUBMITTED_UNCONFIRMED` (`POLICY_APP_START_STATE_UNKNOWN`) and is never released. No submit
  is dispatched after such an error. If `confirm` raises after this run verified the exact
  employer acknowledgement, the result follows the ledger: `EMPLOYER_CONFIRMED` only when the
  row reads `Confirmed`, otherwise `SUBMITTED_UNCONFIRMED`.
- The browser is visible (`headless=False`) so the owner can solve a CAPTCHA/MFA
  challenge; the engine never solves one.
- Only SmartRecruiters is supported; any other ATS family is `BLOCKED`.

## Mac installation (GD-006 step 6; the owner runs these)

From the repo checkout on the Mac, with its venv active:

```sh
git pull                                   # main with GD-006 steps 1-5
python -m pip install -e '.[test]'
python -m playwright install chromium
python -m ejs.apps.policy_engine --prepare # owner-only folders, incl. logs/
python -m ejs.apps.policy_engine --launchd-plist > ~/Library/LaunchAgents/com.ejs.policy-engine.plist
chmod 600 ~/Library/LaunchAgents/com.ejs.policy-engine.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ejs.policy-engine.plist
python -m ejs.apps.policy_engine           # dry run: prints {"status": "DISABLED"}
```

The agent runs at 08:30 Mac local time (set the Mac to Europe/Istanbul), only while the
owner is logged in, with a visible browser. It stays harmless until the owner writes
`control/runtime.json`. To remove it: `launchctl bootout gui/$(id -u)/com.ejs.policy-engine`.
Before reinstalling, run that `bootout` first (a second `bootstrap` of a loaded job fails).

Missed runs: a run missed while the Mac sleeps fires once on wake (possibly late); a run
missed while it is off or logged out is skipped. The log (`logs/engine.log`, 0600) is not
rotated; it holds status codes only.
