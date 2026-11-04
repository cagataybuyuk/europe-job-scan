# Retained fenced application session — TEST engineering

## Problem and resulting behavior

The old prepare-only entry point always opened a new browser, inspected, filled
and closed it. A review interval could not preserve the exact page. The new
owned-session API separates inspection from one preparation attempt while keeping
that same browser/context/page alive in memory. The existing `execute(request)`
entry point remains a compatibility wrapper over the same implementation.

```python
with executor.open_session(target_url) as session:
    observed = session.inspect()
    # Separate policy review + independently pinned manifest/facts validation.
    # Do not prepare on observed.error_code or on an unverified vacancy.
    result = session.prepare(
        approved_request,
        reviewed_observation_id=observed.observation_id,
    )
```

The observation ID only binds the caller's reviewed snapshot to this session. It
is NOT user authorization, a reviewer identity, an approved candidate fact, or
permission to grant additional network access. The caller must still validate
scoped authority, approved facts/CV, exact job target and independent review. The
live CLI's existing pinned manifest/facts checks remain required.

## Boundaries

- Session owns the browser lifecycle and installs the unchanged context fence
  before creating a page. It does not accept a page from CDP or another caller.
- The observation contains controls and structural metadata, never input values,
  CV bytes, cookies, email codes or storage state. Return values do not expose the
  internally retained snapshot containers.
- A new observation supersedes the old token. A token from another session,
  target drift, changed locator identity/structure, stale observation or expired
  session prevents preparation. Timeouts use a monotonic clock and are checked
  on API operations; there is no background watchdog. The owner must close the
  context on cancellation or an idle review deadline.
- Counters and network-denial state survive inspection and failures. Only one
  preparation attempt is allowed; failed or ambiguous effects require
  reconciliation. Closing a session cannot reset/reopen it. Final readiness is
  rechecked after browser validity callbacks; a late denied request cannot be
  reported as pre-submit ready.
- The current document writer still refuses Shadow DOM, any frame, duplicate
  locators and Next/Continue/Proceed structures. Observation traversal indices
  are not durable write selectors. Scoped step navigation remains a later unit.
- No storage-state/profile export, general credentials, new OAuth permissions,
  CAPTCHA bypass, employer upload/autosave endpoints or final submit is added.
  All HTTP remains blocked once preparation starts.

Retaining page identity is not proof of authenticated-session or CAPTCHA reuse.
The fence still denies challenge POSTs and unreviewed external resources. Live
challenge recovery and actual employer application completion remain unverified.

## Manual-only workflow validation fix

PR #88 was approved on exact head `1b1574570a0d147af50711963d45f7fde0c489e7`
and merged by `bykcgty` as `9497d487df3ddb87b5830d343033b2438d51f4c0`.
Post-merge run `41907062243` failed before creating any job because the canary
workflow used `runner.temp` in job-level `env`, where that context is unavailable.
Previous passing PR test jobs did not validate this manual-only workflow.

The input directory is now initialized on the assigned runner using `RUNNER_TEMP`
and `GITHUB_ENV`. Secret inputs remain outside the checkout, owner-only, exact-byte
hash pinned, and removed by guarded cleanup. Immutable SHA is passed via a quoted
environment variable rather than interpolated into shell source.

`bash scripts/ci/lint_workflows.sh` checks ALL workflows during the existing
static-guards job, including manual-only canaries. It downloads official
`actionlint` 1.7.12 with platform-specific pinned SHA-256 verification, runs it
locally, and removes the temporary tool directory. No workflow or ATS action is
executed by linting. The original context error was reproduced before the fix;
all repository workflows passed after it.

Primary references:
https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability
https://github.com/rhysd/actionlint/releases/tag/v1.7.12
https://github.com/rhysd/actionlint/blob/main/docs/usage.md

## Current target research

The public employer Posting API returned Version 1 Senior Business Analyst
`REF7089X` / `744000159638089` as active on 2026-11-04, released that day.
It is a discovery candidate, NOT a promoted execution target: vacancy-specific
sponsorship is unstated and mandatory fit remains incompletely verified.

https://api.smartrecruiters.com/v1/companies/Version1/postings/744000159638089

Public read-only posting metadata does not authorize the protected Application
API. See https://developers.smartrecruiters.com/docs/endpoints . No application,
CV or candidate data was sent during this research.

## Local validation

Final combined regression: **143 passed, zero skipped**, using real managed
Chromium and synthetic local fixtures (101.70 seconds). This includes the existing
114 preparation/manifest/file/policy cases and 29 retained-session cases.
All workflows passed actionlint; secret/authority scans and diff checks passed.
Independent code review reproduced the late-readiness defect, then verified its
fix and final guard. These are engineering proofs, not live application acceptance.
