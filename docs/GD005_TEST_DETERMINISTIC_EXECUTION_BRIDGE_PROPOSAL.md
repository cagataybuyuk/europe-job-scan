# GD-005 — TEST-only deterministic execution bridge (PROPOSAL)

Status: **DRAFT / REVIEW REQUIRED — NO IMPLEMENTATION OR DEPLOYMENT AUTHORITY**  
Date: 2026-11-02  
Issue: #65  
Design parent: Drive GD-005 v1.0; existing TEST-only advisory router #63 and synthetic CLI #64.

## 1. Purpose and current baseline

Keep EJS making measurable engineering progress when Work/Codex quota is exhausted, through independently approved, deterministic offline jobs on GitHub CI or MacBook M2.

Existing `ejs.services.resource_router.route` is **purely advisory**. The synthetic CLI selects a declared provider but never executes it; `provider_executed=false`, `test_evidence=not_run`, `permitted_actions=[]`, `final_submit=false`. Its caller-supplied `approval_ref` is **not proof of approval**, and `checkpoint_ref` is **not execution evidence**. This contract must not alter those meanings.

The repository main baseline at drafting is `0c75c38fa7259d46635a4f4f998ac1a4a191a8a8`, with successful GitHub CI and Windows checks. A successful existing test run is not a new deployment or external website test.

## 2. Split of authority

1. **Planner/advisory router:** may recommend `github_ci`, `local_macbook` or `human_review`. Must not grant execution rights.
2. **Independent approval gate:** a reviewed, TEST-only static task manifest is pinned to exact repository SHA and a specific allowlisted task ID. The runner must verify this manifest, not merely accept a free-text approval reference.
3. **Trusted executor:** may invoke only the exact command vector associated with a pre-reviewed task ID. It must not evaluate a model-generated command, use `shell=True`, expand arbitrary user paths or infer permissions from the router's selected provider.
4. **Evidence consumer:** records the actual process exit code, source SHA, allowlist revision, elapsed time, evidence hashes and outcome without storing secrets. A provider selection alone cannot be reported as a test pass.

No part of this proposal authorizes live ATS action, identity/session handling, PROD spreadsheet changes, deployment or paid infrastructure.

## 3. Proposed manifest (synthetic schema; not an active credential)

```json
{
  "schema_version": "GD005-OFFLINE-EXEC-1",
  "environment": "TEST",
  "task_id": "pytest_core",
  "source_sha": "<exact-40-hex-sha>",
  "allowlist_revision": "v1",
  "approval_state": "review_required",
  "network": "denied",
  "workspace": "isolated",
  "max_wall_seconds": 300,
  "max_retries": 0,
  "payload_class": "synthetic_only"
}
```

`approval_state=review_required` **must refuse execution**. No approval state should be trusted solely because a caller included its string value; establish the authoritative, reviewed manifest publication/verification path separately before implementation.

The first allowlist should be deliberately small and fixed: `pytest_core` (existing unit/core tests), `secret_scan` and `authority_boundaries`. Bind to known repository scripts and explicit argument arrays, not arbitrary paths. Each additional task requires review and regression tests. Keep existing CI as the canonical executor when available; local MacBook is a separately verified fallback.

## 4. Execution and data boundaries

- Environment must equal `TEST`; explicit manifest + exact SHA are required. Fail closed on missing, stale, ambiguous, malformed, duplicated or unapproved work.
- Default mode is **read-only/dry-run**. Only reviewed offline tests are candidates for execution. No write to production or TEST operational Sheets from this bridge.
- Use an isolated temporary workspace; do not scan the user's home directory or import browser profiles. Keep logs restricted to synthetic test evidence; redact/deny credentials, cookies, OTPs, CVs, identity data, employer form data and personal secrets.
- Network access is denied by policy and tested at runtime, not merely assumed from a CLI flag. No browser authentication, employer navigation, upload, consent, submit or CAPTCHA/MFA interaction. High-risk/interactive tasks go to `human_review`.
- Restrict timeout, concurrency (one), retries (zero by default), and runner privileges. No auto-paid API, Cloud Run activation, or self-hosted runner credentials within this scope.
- Existing `EJS_FINAL_SUBMIT=false`, kill switch, fingerprint, idempotency, readback, TEST/PROD separation and release controls remain independent and unchanged.

## 5. Required acceptance matrix

| Case | Expected |
| --- | --- |
| Exact reviewed TEST manifest + source SHA + allowlisted offline command | Executes that exact command, records real exit/evidence |
| Advisory router selects provider without independently approved manifest | Refuse; no execution |
| Missing/invalid approval, PROD, unknown task, wrong SHA, wrong allowlist version | Refuse; no execution |
| Task attempts network, browser auth, external form action or filesystem escape | Refuse/block; no external side effect |
| Task contains sensitive fields or unreviewed command arguments | Refuse; no secret emission |
| Timeout, crash, duplicate/retry, partial evidence or nonzero exit | Failure/needs review; never fabricate PASS |
| Local Mac fallback unavailable | `human_review` unless an independently authorized CI runner is verified |

Proof must include a local synthetic/offline smoke, remote GitHub CI result for the exact reviewed SHA, security guards, and independent readback. `test_evidence=not_run` must not be elevated to a passing result.

## 6. Delivery plan / decision gate

1. Review and approve this contract as a GD-005 follow-up or new GD in canonical Drive governance. **This draft PR is not approval.**
2. In a separate, TEST-only implementation PR, add a minimal manifest validator and hardcoded command allowlist. Preserve router purity.
3. Add deny-path regression tests before any execution tests.
4. Verify Mac M2 offline + GitHub CI with exact-head readback; record TEST result and 02_Guncel_Durum update.
5. Consider merging only after review. Actual provider dispatch beyond offline allowlisted tests, employer interaction and deployment are separate later gates.

## 7. Out of scope

ADP PR #60 (currently broad and draft), persistent sessions, OTP, browser write, CV upload, ATS submit/Applied mutation, PROD tracker modifications, paid models/APIs, and rollout of the daily job scanner. These must not be bundled into this bridge.

## 8. Review questions

- What trusted publication mechanism will attest task-manifest approval independently of arbitrary caller input?
- Which exact offline commands and argument vectors qualify for initial allowlisting, with measured runtimes on Mac and GitHub?
- Can network denial and temporary-workspace isolation be enforced on both platforms without elevated privileges?
- How will retry/duplicate job identity and immutable evidence be persisted without writing to business-state Sheets?

References: https://github.com/cagataybuyuk/europe-job-scan/issues/65 ; https://github.com/cagataybuyuk/europe-job-scan/pull/63 ; https://github.com/cagataybuyuk/europe-job-scan/pull/64 ; https://docs.google.com/document/d/1LppXUfKPa0-w6c2Q21VMgYtQ1_ZXxQ8Pl0ByvuvsesI/edit


## 9. Design decision record v0.2 — trust, isolation and phased delivery (2026-11-02)

**Status remains DRAFT / REVIEW REQUIRED.** These recommendations close design ambiguities; they do not approve code, activate any runner or grant production rights.

### A. Verified constraints

- The GitHub branch API currently reports \`main.protected=false\`; the repository rulesets list was empty. The separate branch-protection endpoint returned HTTP 403 to the connected app, but the ordinary branch response is sufficient to flag the current absence of a verified protected-main boundary. Do **not** call a manifest committed to the current main branch an independently trusted execution approval.
- Existing \`.github/workflows/ci.yml\` runs five jobs and uses \`permissions: contents: read\`. There is no special new bridge workflow yet. PR #66's five CI jobs and the Windows regression job passed (6/6), but this does not verify an execution bridge.
- User-supplied MacBook M2 output reports 77/77 router tests and, later, secret-scan PASS, authority-boundary PASS and 220/220 core tests in 0.68 s. The 77 tests overlap with the 220 suite and must **not** be added together as unique test count. Latest local output did not reprint HEAD SHA; prior verified main SHA was \`0c75c38fa7259d46635a4f4f998ac1a4a191a8a8\`.
- Current routing CLI continues to report \`provider_executed=false\`, \`test_evidence=not_run\`. Report the **real manual pytest result** separately.

### B. Recommended two-lane fallback (immediate vs. gated)

**Lane 0 — usable now without new authorization:** keep the existing GitHub CI on PR/main and the human-started Mac \`.venv/bin/python -m pytest\` / static-guard commands. Record the exact observed SHA before each run, result and duration. No new workflow, service, tokens, container or PROD/TEST Sheet writes. This sustains engineering work with no Work/Codex dependence.

**Lane 1 — proposed, blocked until a new canonical GD approval:** add a separate TEST-only executor of approved, deterministic offline test tasks. Preserve the existing advisory router unchanged; never convert selected provider, arbitrary \`approval_ref\` or a checkpoint hash to execution authority. The earliest implementation step should be a **validation-only, no-process-spawn** contract and deny-path tests. Add process execution only after the specific authority and isolation gates pass.

### C. Trusted approval root (decision required before code)

1. Preserve a human decision recorded as **Approved** in canonical Drive GD governance; a GitHub PR comment, caller string or draft PR is not approval.
2. Before allowing GitHub-driven unattended execution, independently verify protection of the default branch and the runner workflow (at minimum: review before merge, prevent force push/bypass by untrusted actors, required checks) or choose another independently trusted publishing mechanism. Treat the current \`protected=false\` state as **BLOCKED**.
3. Avoid the self-referential source-hash trap: an approval for source SHA \`S\` must live in a *separately published and independently reviewed* record that can refer to \`S\`. A self-declared \`approved=true\` inside commit \`S\` is never sufficient.
4. Pin both source SHA and the reviewed workflow/runner implementation identity, task-ID allowlist revision, approved environment and approval record. Validate these on the trusted side of the execution boundary. Reject stale, contradictory or unknown entries.
5. Until the protection/attestation route is independently checked, **human-initiated commands are the only fallback execution path**. GitHub's existing ordinary CI is a separate established regression path, not the new approval mechanism.

### D. Minimal reviewed task catalog (candidate allowlist)

| Task ID | Fixed command form | Data / scope |
| --- | --- | --- |
| \`secret_scan\` | \`python scripts/ci/secret_scan.py\` | Repository-only static check |
| \`authority_boundaries\` | \`python scripts/ci/verify_authority_boundaries.py\` | Repository-only static check |
| \`pytest_core\` | \`python -m pytest -q\` followed by the **exact 17 named paths** from current \`.github/workflows/ci.yml\` | Existing unit/core tests; no browser fixture download |

Command vectors must be hardcoded and reviewed against a pinned source/workflow revision. Never accept a supplied shell string, dynamic path, arbitrary \`-m\` target, URL, additional pytest flags, task script content or third-party action from a runtime task request. No \`shell=True\`, GitHub CLI mutation or employer-side task is part of this catalog.

### E. Network and filesystem boundary

- **GitHub execution candidate (not implemented):** use an explicitly reviewed prebuilt image identified by digest. Run the task process in a dedicated Linux container with \`--network none\`, non-root UID, a read-only reviewed-source mount, read-only root filesystem, no GitHub token/secrets, no host Docker socket, reduced privileges/capabilities, bounded tmpfs, timeout and concurrency. The container's loopback interface still exists; \`--network none\` specifically denies external network reachability. Verify with a negative network test.
- Any image build / dependency download requires separate handling **before** the network-disabled task stage. Do not claim those setup stages were network-isolated. Avoid running unreviewed repository install hooks in a network-enabled preparation stage.
- **Mac fallback:** a normal Python venv is **not** a security sandbox. Continue the proven manual tests, but **do not** offer automatic offline dispatch on macOS until an installed and verified sandbox/container equivalent passes the same deny-path tests. Docker availability on this Mac was not verified.
- No mount of home folders, browser profiles, SSH keys, application assets, Google tokens, secrets or production tracker files. No employer navigation, session reuse, form write, upload or submit.

### F. Evidence and failure contract

Each actual execution receipt must distinguish \`provider_selected\` from \`provider_executed\` and include: exact source SHA, approved manifest identity/digest, approved runner/workflow identity, allowlist revision, task ID, platform, run ID, start/end time, exit code, outcome and bounded output digest. Store only minimal synthetic/test evidence; never raw credentials or personal/browser content. Do not mistake a hash, log entry or CI status for immutable attestation.

Use a deterministic job key based on \`source_sha + manifest_digest + task_id\`, refuse ambiguous duplicates and prevent overlapping executions. A crash, timeout, unexpected network reachability, filesystem escape, bad approval, missing evidence or nonzero exit is FAIL/REVIEW, not PASS. No automatic retry in v1.

### G. Acceptance and go/no-go

1. **Already green:** existing manual Mac guard checks and core pytest; GitHub PR #66's unchanged 6/6 CI jobs.
2. **Next independent decision:** approve or reject the new GD scope and a trustworthy authorization publication method. \`main.protected=false\` must be resolved or explicitly replaced with a separate trusted gate before an executor is enabled.
3. **After GD approval:** first PR adds schema/validation and deny-path tests only (no process spawning); second tightly scoped PR, only if isolation is verified, adds the fixed command execution and real evidence.
4. **No-go:** if network isolation or trusted approval cannot be demonstrated on a runner, retain Lane 0 and do not automatically dispatch jobs. Never weaken GD-005, TEST/PROD or final-submit controls to make the bridge pass.

Technical references:
- GitHub Actions manual triggers: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
- Docker none network driver: https://docs.docker.com/engine/network/drivers/none/
