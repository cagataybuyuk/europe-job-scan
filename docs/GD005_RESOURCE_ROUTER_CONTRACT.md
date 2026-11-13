# GD-005 — Resource Router Contract (TEST-only design)

Status: proposal / design only. Governance: [GD-005](https://docs.google.com/document/d/1LppXUfKPa0-w6c2Q21VMgYtQ1_ZXxQ8Pl0ByvuvsesI/edit). No production authorization.

## Purpose
Keep EJS engineering moving when ChatGPT Work/Codex quota is constrained. Model usage is optional for execution; deterministic CI, GitHub Actions, Python and Playwright remain usable independently.

## Routing policy
- **High-complexity design:** Work or Codex, only when available and worthwhile.
- **Bounded edits / review:** economical available model or deterministic scripts.
- **Quota low or exhausted:** GitHub-hosted CI, existing Apps Script control plane, local MacBook M2 fallback when available. Do not automatically purchase credits or enable a paid API.
- **Manual remaining-quota bands:** >=50% normal; 20–49% economical; <20% local-first. These are policy thresholds, not verified official limits. No automatic quota telemetry is assumed.

## Proposed interface (not implemented)
Input: task_id, environment, risk_level, required_capabilities, quota_band, network_requirement, approval_ref.
Output: provider, fallback_provider, permitted_actions, stop_reason, checkpoint_ref.
The router MUST NOT grant execution authority. It only selects a permitted provider within the existing Apps Script/control-plane authority contract.

## Hard invariants
1. TEST and PROD state are isolated. Never mutate PROD via a TEST task.
2. EJS_FINAL_SUBMIT remains false; no final submit, Applied state mutation, or employer form write/upload without a separately approved gate.
3. Kill switch, fingerprint, idempotency and readback guards remain authoritative across providers.
4. No CAPTCHA/MFA bypass, no raw cookies/tokens/PII in GitHub artifacts or logs.
5. A provider switch cannot silently change business policy or source of truth.
6. Fail closed if provider capabilities or authorization cannot be verified.

## Acceptance tests to implement later
- Exhausted-quota simulation selects deterministic CI/local fallback without a paid API.
- Provider switch preserves TEST-only and final-submit=false boundaries.
- Unsupported browser operation yields human_review, not false success.
- Retry preserves idempotency; checkpoints record task, commit, tests, next action.
- MacBook-unavailable mode permits documentation and GitHub-hosted TEST CI without local setup.

## Rollout
A. Review and approve this contract in draft PR.
B. Implement TEST-only router unit tests and config (separate reviewed change).
C. Verify GitHub CI and no PROD mutation.
D. Run local MacBook fallback smoke last, when device is available.

No CI, runtime, deployment, or production behavior is changed by this document.
