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
