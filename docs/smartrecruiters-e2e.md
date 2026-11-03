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

## MVP readiness correction — 2026-11-03

Real anonymous read-only inspection of a tracked target displayed an expired-job
status, one hidden share input and zero application mutations. Earlier discovery
counted any control and suggested manifest review even though the observed input
was hidden sharing UI. The captured report remains local/private; no profile,
CV, cookies or user data was supplied to the live page, and tracker cells were
not modified. Historical tracker availability was not treated as current proof.

Discovery now requires visible controls and recognizes the exact observed
visible expired-job status. Read-only routing v2 independently rejects expired
or auth-boundary evidence even when the reported runtime state says inspected;
hidden-only controls cannot become manifest candidates. Missing mutation
counters are rejected, and explicit clear challenge evidence is needed before
manifest review. These decisions still grant no safe-fill/upload/submit rights.

Local baseline: 106 existing preparation/browser/upload/submit-policy tests
passed against installed Chromium (not skipped). After the correction, 73
targeted preparation, real DOM, shadow, route and policy tests passed. These
sets overlap and must not be added as unique tests. A real Chromium regression
uses an expired status plus hidden sharing input and proves zero DOM mutation;
replaying the captured live structural evidence now returns JOB_EXPIRED and
manifest_review_allowed=false without another navigation.

Remaining gate: choose a currently verified open application target, obtain
current application controls rather than posting/share UI, review the manifest,
then separately validate safe-fill/upload. Submit and confirmation are still
unimplemented release-gated work. No successful live preparation or submitted
application is claimed by this correction.

The read-only CLI/workflow can record JOB_EXPIRED and AUTH_BOUNDARY as successful
observations while routing refuses manifest promotion. Observation exit 0 never
means a job is open, preparation succeeded or execution was authorized. Unsupported
runtime remains a nonzero exit; no workflow input or runtime authority is added.
