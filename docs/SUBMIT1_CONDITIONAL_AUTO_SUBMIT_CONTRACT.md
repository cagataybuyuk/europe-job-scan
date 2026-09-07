# SUBMIT-1 — Conditional Auto-Submit Contract

Roadmap v1.1 was approved on 2026-08-21. SUBMIT-1 converts approval decisions A1-A10 into executable policy and pre-submit validation semantics. This package **does not click Submit**; live browser execution authority remains OFF until BE-1 and the staged R2 single-opportunity canary.

## Policy decisions encoded

- A1: conditional auto-submit is authorized only when every SUBMIT-1 gate passes.
- A2: work authorization and sponsorship answers may use explicit verified facts only; never infer or optimize.
- A3: salary auto-answer requires a pre-approved country/role policy reference; otherwise block.
- A4: required privacy-processing acknowledgement may be accepted only when policy-covered.
- A5: optional marketing/talent-pool consent defaults to No / blank.
- A6: optional demographic fields remain blank; mandatory fields may use only an approved neutral choice when available.
- A7: AI motivation / cover letter is allowed only when grounded and provenance-complete.
- A8: CAPTCHA/MFA bypass is forbidden; challenge blocks unattended submit.
- A9: rollout is 1 live canary, then 5/day until a later approved expansion.
- A10: MW-5 continues as a parallel event-gated safety track and does not block browser/submit engineering.

## Pre-submit hard gates

1. Exact execution, opportunity, requisition, candidate-profile and apply-URL identity.
2. Fresh runtime inspection and deterministic form fingerprint.
3. No detected template drift; stale tracker fingerprint aborts.
4. Every required control mapped at High confidence.
5. Every required answer resolved under verified fact / approved policy / grounded-generation / approved artifact semantics.
6. Complete provenance for every required value or action.
7. Required document artifacts have immutable references.
8. Deterministic submit key is unique.
9. No CAPTCHA/MFA/unsupported technical challenge.
10. Rollout daily cap not exceeded.
11. Browser external-form authority + final-submit flag ON and kill switch OFF.

## Applied-state rule

Clicking Submit is not evidence of a successful application. `Applied` reconciliation remains forbidden until exact confirmation evidence (confirmation page, employer email or ATS acknowledgement) is linked to the same submit/execution/requisition identity.

## Idempotency

Submit key:

`submit:<opportunity_key>:<ats_requisition_id>:<candidate_profile_version>`

Validation key:

`submitval:<execution_id>:<form_fingerprint>:<policy_version>`

Exact retry is a no-op / duplicate-suppressed. Immutable-key collision with changed payload fails closed.
