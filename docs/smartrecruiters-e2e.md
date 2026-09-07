# SMARTRECRUITERS-EXEC-0.1 — End-to-End Preparation Runtime

Status: Local runtime PASS / production live egress pending.

## Scope

A single Playwright/Chromium page performs:

1. current form inspection and required-control inventory,
2. exact form fingerprint verification,
3. whole-plan fail-closed preflight,
4. PREFILL-EXEC-0.2 safe field writes with exact readback,
5. FILE-UPLOAD-0.1 approved PDF attachment with browser byte-hash readback,
6. post-action fingerprint verification,
7. browser form validity check,
8. SUBMIT-1.0 pre-submit validation,
9. stop before final Submit.

The executor deliberately contains no submit-click path. Consent, credential entry, protected attributes and CAPTCHA bypass remain outside its authority.

## Canary

Run: `SRE2E-20260822-1105`

- 7 observed required controls / 7 resolved.
- 7 safe field writes / 7 exact readbacks.
- 1 approved production CV asset / 1 exact filename, size, MIME and SHA-256 readback.
- Form fingerprint stable before and after preparation.
- Browser form validity: true.
- Submit control observed: true.
- SUBMIT-1 policy eligibility: true.
- Rollout state R1: `Eligible / Runtime Disabled`.
- Submit clicks: 0.
- Exact artifact retry: duplicate-suppressed.

## Live gate

Current tracked Rituals SmartRecruiters requisition `744000112936917` remains a valid To Apply route, but Chromium in the current host is blocked before employer DOM render with `ERR_BLOCKED_BY_ADMINISTRATOR`. No live fill, upload, form inference or submit is allowed without a trusted current form fingerprint.

## Production runtime requirement

The next live gate requires an outbound-enabled Linux runtime able to run Docker/Chromium and persist encrypted browser/session state. Secrets, cookies and credentials must never be written to Sheets or source control.
