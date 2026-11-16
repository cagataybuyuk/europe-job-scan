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
opened (`RUNTIME_DISABLED` / `KILL_SWITCH`). `STOP` is re-read between items.

## Result file (EJS-ENGINE-RESULT-1)

`schema_version`, `execution_id`, `manifest_sha256`, `status`, `code`,
`submit_clicks` (1, 0, or -1 when dispatched but unverified), `evidence_ref`
(hash reference only), `observation` (observe pass: next page `step`, `final`,
`fields` structure without answers), `finished_at`. No candidate values or CV bytes.

Statuses: `EMPLOYER_CONFIRMED`, `SUBMITTED_UNCONFIRMED`, `OBSERVED`, `BLOCKED`,
`KILL_SWITCH`, `RUNTIME_DISABLED`, `MANIFEST_REJECTED`, `DUPLICATE`, `CAP_REACHED`,
`BUSY`, `LEDGER_ERROR`, `INTERRUPTED`.

## Rules

- One engine at a time (`engine.lock`).
- Unsafe or oddly named inbox files are never read or moved (`SKIPPED_UNSAFE`).
- A manifest left in `processing/` by a crash is never re-run: it is reported as
  `INTERRUPTED`, or `SUBMITTED_UNCONFIRMED`/`EMPLOYER_CONFIRMED` if the ledger shows a
  dispatch, then archived. Claude decides on any new attempt.
- The browser is visible (`headless=False`) so the owner can solve a CAPTCHA/MFA
  challenge; the engine never solves one.
- Only SmartRecruiters is supported; any other ATS family is `BLOCKED`.
