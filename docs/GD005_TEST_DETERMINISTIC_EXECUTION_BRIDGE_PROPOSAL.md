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
