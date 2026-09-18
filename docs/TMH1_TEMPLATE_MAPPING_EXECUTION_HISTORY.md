# TMH-1 — Template, Mapping & Execution History

Status: implemented foundation, no browser or submit authority.

TMH-1 decouples discovery source from application execution platform and introduces five persistent concepts:

1. Canonical Application Schema — platform-independent field contract.
2. Application Template Registry — versioned ATS/employer behavior accelerators.
3. Field Mapping Registry — versioned observed-control → canonical-field semantics.
4. Application Execution Log — append-only per-execution event lineage.
5. Submission Artifact Registry — immutable references to exact CV/cover-letter/answer/policy/template/confirmation artifacts.

Core invariant: templates are accelerators, not executable truth. Every execution must perform live inspection. A live fingerprint mismatch invalidates the stored template baseline and forces re-inspection; stale templates never override runtime evidence.

Promotion rule: a one-off runtime observation does not become a reusable mapping. High-confidence mappings need repeated stable evidence (default >=3) or explicit review. Employer overrides are only for recurring employer-specific behavior.

TMH-1 authority is metadata/history only. Applications, User Facts, external forms and final Submit remain outside this package.


## Multi-ATS production role

TMH-1 is the shared persistence layer for the first-launch ATS set: LinkedIn Easy Apply, SmartRecruiters, Workday, Greenhouse, Lever and ADP Workforce Now.

The registry must keep **source identity** separate from **execution adapter identity**. For example, a LinkedIn-discovered job that resolves to Workday records LinkedIn as the discovery source while Workday owns the application template, mapping, browser execution and submit/confirmation lineage.

For launch, every execution history record should bind at minimum:

- discovery source and source job ID;
- canonical apply URL;
- ATS family and adapter version;
- employer/requisition identity when observable;
- live form/surface fingerprint;
- template and mapping versions actually used;
- candidate profile and answer-policy versions;
- approved document artifact versions;
- submit eligibility/result;
- confirmation evidence or structured block/review reason.

A failed or blocked execution is first-class history. Repeated failures may improve templates and diagnostics, but may not silently promote broader mutation authority.

The production orchestrator uses this history for duplicate suppression, exact retry/idempotency, drift detection and `human_review` continuation.
