# Local first-application execution lane

The reviewed native SmartRecruiters flow can now run through multiple pages,
upload the actual approved CV bytes, validate every answer against pinned facts,
record the sole durable intent, click once, and reconcile an employer acknowledgement.
This is a new local lane. Existing prepare-only executors and GitHub/Apps Script
TEST workflows retain their existing authority and network fences.

## Entry point and inputs

`python -m ejs.apps.single_application --help` describes the bootstrap. By default
it only validates private input pins and the CV; it opens no browser and creates
no submission ledger. Execution additionally requires
`--execute-reviewed-first-application`, an independently reviewed source commit
and two separately pinned private files. The source worktree must be clean.
There is no API key, model endpoint, purchase, environment-wide submit flag or
cloud deployment in this lane.

The manifest schema is `EJS-SINGLE-APPLICATION-1`; the fact schema is
`EJS-VERIFIED-FACTS-1`. Inputs must be owner-only files in owner-only directories.
Manifest and fact hashes are supplied outside those files. Every field value and
source reference must match a fresh, approved fact with the same exact type.
Salary, privacy, neutral demographic handling and work-right answers have explicit
policy/source rules. Profile assertions are read-only and source-matched.

The trusted review also pins the target/publication, current public posting bytes,
role fit and mandatory requirements, complete form structure, visible labels,
options, selectors, exact read URLs, upload/Next/Submit controls and success marker.
The compiler checks source consistency; it cannot authenticate a reviewer or
convert a claimed source reference into independently verified truth.

The first host bootstrap is pinned to the approved Version 1 requisition. It uses
the previously established canonical private state directory under the user's
`Local/Projeler/EJS/.ejs-local`, shared across worktrees and execution/profile
versions. There is intentionally no `--ledger` or reset/retry option.

## Actual execution and evidence

- The driver owns a fresh Chromium context, blocks service workers and leaves
  intercepted WebSockets disconnected. All reads are exact-allowlisted; all writes
  are denied except one armed resume POST and one armed application POST.
- Every page is observed before and after writes. Required unknown controls,
  structural changes, reactive overwrites, unexpected writes, frames/challenges,
  unsupported profile contents and failed readbacks stop preparation.
- The CV is read through a checked file descriptor. The hash of the actual bytes
  supplied to Playwright, vendor attachment acknowledgement and visible filename
  are bound together. This does **not** claim an independent hash of bytes later
  downloaded from the employer's attachment store.
- The SUBMIT-1 policy is evaluated on the complete reviewed control set. SQLite
  reservation and uncertainty are committed before the driver may click Submit.
  A crash or ambiguous outcome permanently consumes that intent; no second click
  or newly created budget is used to recover it.
- Even vendor JavaScript retrying the application POST is blocked after the first
  allowed request. A matching HTTP 200/201 response with literal JSON `success:true`
  and the matching visible success view are required for confirmation. Other
  response schemas remain unconfirmed until independently reviewed.
- Only a matching Confirmed ledger can publish the private local `Applied`
  outcome. This does not write the production Applications spreadsheet.

## Current limits and live acceptance

The browser tests use synthetic candidates and locally fulfilled HTTPS routes.
They prove the integrated native flow and real browser/SQLite behavior; they are
not employer receipts. A successful live application is a separate acceptance
gate, requiring fresh independent review and employer confirmation.

Apply with LinkedIn/new OAuth is an explicit handoff boundary. This command does
not import browser cookies, grant permissions, solve challenges or silently fall
back to native entry. The retained-return service described below is tested
separately; a human-auth network/bootstrap bridge and reviewed profile corrections
remain necessary for unattended use on that surface.
The trusted interactive CUA first-canary lane is separately reviewed and must not
be represented as this command having completed a live application.

On 2026-11-07 that separately reviewed interactive lane completed the first
Version 1 REF7089X application. The employer success page and matching
role/employer/recipient confirmation email were observed, and the canonical
ledger is Confirmed with a private Applied outcome. The email did not contain the
exact requisition/publication identifiers. This consumed the one live application
scope; subsequent development must not create another ledger or submission.

On 2026-11-11 the owner-authorized reconciliation updated the matching existing
Applications row 107 to Applied, with Applied Date 2026-11-07. The connector and
native Sheet readbacks matched, historical review notes were preserved, and
sponsorship remained UNKNOWN. This was a controlled manual reconciliation of
existing authenticated evidence, not a new application or automated CLI Sheet
write. The private reconciliation receipt is kept with the original evidence.

The ledger guarantees one final intent/click across concurrent runtimes. Competing
processes can prepare and upload before reservation; unattended host scheduling
must serialize preparation as well. Cross-host/cloud operation requires a reviewed
shared ledger. ADP, multi-job rollout and general production release are outside
this first scoped lane.

## Bounded reviewed entry return (TEST service API)

`preferred_entry()` reports `APPLICATION_LINKEDIN_HUMAN_HANDOFF_REQUIRED` for the
exact visible Apply with LinkedIn action. It also recognizes a narrow public
LinkedIn widget URL, bound to the SmartRecruiters origin, even when its document
cannot load. This recognition does not click the widget, grant OAuth permissions
or add network endpoints. Other frames and challenges remain blockers.

The runtime reports `HUMAN_HANDOFF_REQUIRED` before filling, uploading or reserving
a submission intent. A trusted caller may retain this same driver and later call
`observe_entry_return()`. Observation requires the owned live page/context, the
exact entry URL, closed extra windows, no challenge and an unused read-only
boundary. It issues a random single-use observation reference with a 120-second
lifetime. A private in-memory snapshot binds returned values and page content;
password/verification-code surfaces are rejected before that snapshot is made.
The public observation contains no field values or page-body digest.

An independent reviewer must inspect the actual returned profile and values
against verified sources. A newly pinned manifest must include
`entry_return_review` with exactly `observation_id`, `surface_fingerprint`,
`return_url`, `reviewer_ref` and `reviewed_at`. The fingerprint/URL bind the first
reviewed step, the reviewer matches the manifest reviewer, and the timestamp is
UTC and at most 120 seconds old. This record is included in the external manifest
hash. A boolean claiming profile match or permission is not accepted evidence.

`SingleApplicationRuntime.resume_reviewed_return(..., observation_id=...)` checks
the same authority, occupied ledger, public posting and CV gates before consuming
the driver's observation. The driver rejects stale, replayed, changed or foreign
sessions, and failed network boundaries remain failed. Acceptance resumes the
existing pipeline without another navigation, LinkedIn click or native fallback.
This service does not attach arbitrary browser pages or authenticate reviewers.
It does not make the CLI an unattended OAuth runner, and no additional live
application was used to test it. Synthetic browser fixtures and private temporary
SQLite ledgers cover this API.

## Verification

Run the four pure suites and the actual Chromium integration suite:

```sh
python -m pytest -q tests/test_single_application_inputs.py tests/test_oneclick_target.py tests/test_single_application_runtime.py tests/test_application_outcome.py tests/test_oneclick_browser.py
```

CI runs pure suites in `core-regression` and the Chromium suite in
`upload-e2e-regression`. Browser dependencies must be installed through the
existing project bootstrap. No test uses the canonical first-live ledger or sends
requests to an employer.
