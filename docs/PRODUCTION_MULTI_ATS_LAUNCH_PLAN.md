# Europe Job Scan — Multi-ATS Production Launch Plan

Status: approved target scope, implementation in progress  
Plan version: 2026-09-18 / v2.0

## Launch objective

The first production launch is not a single-ATS MVP. The launch target is a daily autonomous application system that can route and apply across the following priority surfaces as far as each runtime safely allows:

- LinkedIn, including external-apply routing and a dedicated Easy Apply adapter;
- SmartRecruiters;
- Workday;
- Greenhouse;
- Lever;
- ADP Workforce Now.

The system must scan daily, deduplicate previously seen/applied jobs, evaluate eligibility, resolve the application platform, dispatch to the correct adapter, fill verified facts, attach approved documents, conditionally submit when every gate passes, reconcile confirmation evidence, and send unsupported or ambiguous cases to `human_review`.

"Broad support" does not mean forcing automation through unsupported controls. CAPTCHA/MFA, credentials that are not already provisioned, unresolved legal/work-right/sponsorship/salary answers, demographic questions, unexpected ATS drift, or unknown required controls remain fail-closed boundaries.

## Production launch definition

Launch is complete only when all of the following are true:

1. A scheduled daily orchestration run can discover and queue opportunities without manual dispatch.
2. Duplicate opportunity/requisition/application identities are suppressed before browser work.
3. Source URLs are resolved into an ATS/application route.
4. The six priority adapters have explicit capability manifests and deterministic dispatch.
5. Each adapter can perform current live inspection before mutation.
6. Verified profile facts and approved policy answers can be mapped through the canonical application schema.
7. Approved CV/document upload is verified by exact browser-side metadata/hash evidence when the ATS supports upload.
8. Final Submit is conditional on SUBMIT-1 gates and adapter-specific live evidence.
9. A Submit click alone never marks the application Applied; confirmation evidence is reconciled separately.
10. Unsupported, ambiguous, challenged, or drifted applications enter `human_review` with a structured reason and reusable evidence.
11. Execution history, template/fingerprint lineage and submission artifacts are written idempotently.
12. Daily results expose counts for discovered, filtered, duplicate-suppressed, attempted, submitted, confirmed, blocked and human-review cases.

## Current baseline

As of 2026-09-18:

| Surface | Current state | Launch work remaining |
| --- | --- | --- |
| ADP Workforce Now | Live inspection/navigation/profile-v2 canary path exists; current work is at the OneTrust preference boundary before Apply | Finish stable cookie path, full application manifest, CV upload, submit canary, confirmation reconciliation |
| SmartRecruiters | E2E preparation runtime exists through inspection, safe-fill, approved document upload and pre-submit validation; historical live-host restrictions are no longer treated as a universal runtime assumption | Revalidate live surface, add reviewed final-submit path, confirmation reconciliation, production adapter |
| LinkedIn external apply | Read-only resolver exists and can route external ATS targets | Promote resolver into daily routing/orchestration path |
| LinkedIn Easy Apply | Currently classified as a human-action boundary | Build dedicated session-aware Easy Apply adapter and canary; never bypass login/MFA/challenges |
| Greenhouse | No production adapter yet | Implement inspect/map/fill/upload/submit/confirm adapter |
| Lever | No production adapter yet | Implement inspect/map/fill/upload/submit/confirm adapter |
| Workday | No production adapter yet | Implement multi-step/session-aware adapter, dynamic field inventory, upload/submit/confirm path |

## Architecture for launch

### 1. Discovery and normalization

Discovery sources may include LinkedIn and other approved sources, but discovery identity is kept separate from execution platform identity.

Normalized opportunity identity:

`opportunity_key + source_job_id + canonical_apply_url + ats_family + ats_requisition_id`

The source layer never directly grants browser mutation or submit authority.

### 2. ATS classifier and adapter registry

Every application target is classified into an adapter family:

`linkedin_easy_apply | smartrecruiters | workday | greenhouse | lever | adp | unsupported`

Each adapter publishes a capability manifest containing at minimum:

- inspection;
- safe field fill;
- document upload;
- multi-step navigation;
- conditional submit;
- confirmation detection;
- session/auth requirement;
- known challenge boundaries.

Unknown hosts or ambiguous fingerprints route to `human_review`, not to a generic click strategy.

### 3. Canonical application schema

All adapters map observed fields to platform-independent canonical fields. Verified user facts, approved policy answers and approved document artifacts are resolved before adapter mutation.

Required unknown/discretionary questions are never guessed. They generate a reusable review item so that a later approved answer can become policy-backed input where appropriate.

### 4. Live inspection before every mutation

Templates accelerate mapping but never replace current evidence. Each execution must:

- inspect the current form;
- compute structural fingerprints;
- compare against reviewed/template expectations;
- inventory required controls;
- stop on material drift before writing.

### 5. Submit and confirmation

Final submit is adapter-local but policy-global:

- SUBMIT-1 eligibility must be true;
- the adapter must have an approved live submit path;
- no challenge/auth boundary may be active;
- all required values must have provenance;
- required artifacts must be verified;
- idempotent submit identity must be unique.

After submit, the adapter performs read-only confirmation reconciliation. Only confirmation evidence can advance the canonical application state to Applied/Confirmed.

## Required implementation phases

### L0 — Scope freeze and common contracts

Status: **implementation foundation complete**; launch acceptance dashboard schema remains to be wired into L6 observability.

Delivered:

- this launch plan;
- adapter capability interface and registry;
- common dispatch/status/error taxonomy;
- structured `human_review` contract;
- production coverage matrix;
- fail-closed LinkedIn source-resolution boundary;
- explicit invariant that L0 dispatch metadata cannot grant mutation or submit authority.

Reference: `L0_COMMON_ATS_ADAPTER_CONTRACT.md`.

No external browser authority change is introduced by L0.

### L1 — Finish ADP vertical

Complete the current ADP path end to end:

`entry -> cookie policy -> Apply -> identity/phone -> application manifest -> safe answers -> CV -> pre-submit -> Submit canary -> confirmation`

ADP becomes the reference implementation for adapter evidence and fail-closed behavior.

### L2 — SmartRecruiters production vertical

Reuse the existing preparation runtime and add:

- current live validation;
- adapter wrapper;
- reviewed submit operation;
- confirmation classifier;
- history/reconciliation binding.

### L3 — Greenhouse and Lever adapters

Implement these on the common adapter contract. Prefer shared primitives where the HTML/control model overlaps, while keeping distinct live fingerprints and submit confirmation logic.

### L4 — Workday adapter

Treat Workday as a multi-step/stateful adapter rather than a generic form. Required work includes:

- step discovery;
- dynamic required-field inventory;
- repeatable/conditional section handling;
- document path;
- session/auth boundary detection;
- submit/confirmation state machine.

### L5 — LinkedIn Easy Apply adapter

Keep the existing external-apply resolver. Add a separate Easy Apply adapter for eligible sessions:

- session-presence detection;
- modal/step inventory;
- verified-field fill;
- document attachment when requested;
- policy-backed screening answers;
- conditional final submit;
- confirmation capture.

Login, MFA, CAPTCHA/security verification and account-recovery flows remain manual boundaries.

### L6 — Daily production orchestrator

Connect all launch adapters into one daily job:

`discover -> normalize -> dedupe -> eligibility -> resolve -> classify -> inspect -> prepare -> submit-or-review -> reconcile -> history`

The orchestrator must enforce per-run/per-day caps, kill switch, retry policy, immutable execution IDs and duplicate suppression.

### L7 — Burn-in and release

Release sequence:

1. one reviewed live submit canary per adapter family;
2. limited production under existing SUBMIT-1 rollout cap;
3. multi-day unattended burn-in;
4. review false blocks, drift and confirmation misses;
5. only then expand caps or unattended coverage.

## Estimated active work

Planning estimate for the current baseline and current collaboration model:

| Phase | Active work estimate |
| --- | ---: |
| L0 common contracts/documentation | 1–2 h |
| L1 ADP completion | 3–5 h |
| L2 SmartRecruiters production | 3–4 h |
| L3 Greenhouse + Lever | 5–7 h |
| L4 Workday | 5–7 h |
| L5 LinkedIn Easy Apply | 4–6 h |
| L6 daily orchestration/history/review queue | 3–4 h |
| L7 production burn-in/fixes | 2–3 h |
| **Total** | **26–38 h** |

This is active engineering/review time, not elapsed calendar time. User-required interaction should be materially lower and concentrated around account/session setup, explicit facts/policies, environment approvals and live canary review.

## Launch blockers vs. post-launch work

A feature is a launch blocker only when it is required for safe daily autonomous operation across the six priority surfaces. UI polish, convenience tooling and optimization are not allowed to delay launch unless they close a correctness/safety gap.

## Nice-to-have roadmap — phased after launch

### N1 — Coverage expansion

Add adapters or resolvers for ATS families such as SAP SuccessFactors, iCIMS, Taleo, Ashby, Teamtailor and Recruitee based on observed opportunity volume.

### N2 — Application quality optimization

- role-specific CV selection/versioning;
- grounded cover-letter generation;
- answer library with employer/country/role policy scoping;
- reusable reviewed screening-answer policies;
- job-description-to-profile evidence matching.

### N3 — Operations and observability

- daily digest;
- failure clustering;
- adapter health score;
- drift alerts;
- execution latency and success funnel;
- review queue dashboard.

### N4 — Template learning and self-healing

- repeated-stability mapping promotion;
- employer-specific overrides;
- structural drift diffing;
- automatic suggestion of mapping/template updates;
- regression fixture generation from sanitized live structure.

No self-healing change may silently increase mutation or submit authority.

### N5 — Search and prioritization intelligence

- role-fit scoring;
- sponsorship-confidence evidence;
- salary/location preference ranking;
- company/role exclusions;
- application-budget allocation when daily caps are active.

Scoring changes prioritization only; it never invents application facts.

### N6 — Reliability and cost optimization

- resilient session storage where permitted;
- queue backpressure;
- adapter-specific retry windows;
- smarter browser reuse;
- artifact retention policy;
- cost/per-application monitoring.

## Scope-control rule

For every proposed development item ask:

> Does this directly increase safe daily autonomous application coverage, required correctness, or launch reliability for LinkedIn, SmartRecruiters, Workday, Greenhouse, Lever or ADP?

If yes, it belongs in L0–L7. Otherwise it is assigned to N1–N6 unless a newly observed production failure makes it a blocker.
