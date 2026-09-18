# SMARTRECRUITERS-EXEC-0.1 — End-to-End Preparation Runtime

Status: Preparation runtime PASS / production live revalidation and submit adapter pending.

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

Historical testing of tracked Rituals SmartRecruiters requisition `744000112936917` observed `ERR_BLOCKED_BY_ADMINISTRATOR` before employer DOM render. That result is retained as target/run evidence, not as a system-wide egress assumption. The production adapter must re-inspect the current target from the approved live runtime and obtain a trusted current form fingerprint before any fill, upload or submit.

## Production runtime requirement

The next live gate is a current SmartRecruiters re-inspection from the approved browser runtime, followed by an adapter-scoped safe-fill/upload validation and reviewed final-submit canary. Any required session material must remain encrypted and outside Sheets/source control.
