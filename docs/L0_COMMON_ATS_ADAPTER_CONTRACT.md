# L0 — Common ATS Adapter Contract

Status: implementation foundation

## Purpose

L0 creates the shared, side-effect-free contract used by the first production-launch ATS families:

- LinkedIn Easy Apply
- SmartRecruiters
- Workday
- Greenhouse
- Lever
- ADP Workforce Now

The contract separates three concepts that must never be conflated:

1. **ATS recognition** — the system can classify a target as a known ATS family.
2. **Implemented capability** — an adapter has code for inspection/fill/upload/etc.
3. **Runtime authority** — a specific reviewed live execution is allowed to mutate or submit.

L0 only owns the first two. It cannot grant browser mutation or final Submit.

## Files

- `src/ejs/contracts/ats_adapter.py`
  - launch ATS enum;
  - adapter capability manifest;
  - implementation/session states;
  - common dispatch state;
  - human-review taxonomy and contract.
- `src/ejs/services/ats_adapter_registry.py`
  - fail-closed HTTPS target classification;
  - launch adapter registry;
  - LinkedIn Easy Apply source-resolution boundary;
  - conversion of non-dispatchable routes into structured `human_review` items.
- `tests/unit/test_ats_adapter_registry.py`
  - launch-family coverage;
  - host classification;
  - planned-vs-implemented capability validation;
  - no mutation/submit authority from dispatch metadata;
  - human-review routing.

## Current capability baseline

| ATS | Implementation state | Inspection | Safe fill | Upload | Conditional submit | Confirmation |
| --- | --- | --- | --- | --- | --- | --- |
| ADP | live_canary | yes | yes | no | no | no |
| SmartRecruiters | prepare | yes | yes | yes | no | no |
| LinkedIn Easy Apply | planned | no | no | no | no | no |
| Workday | planned | no | no | no | no | no |
| Greenhouse | planned | no | no | no | no | no |
| Lever | planned | no | no | no | no | no |

This table describes implemented code capability, not production authorization.

## Dispatch behavior

Known targets with a current inspection-capable adapter may be dispatched to that adapter's existing inspection path. Planned adapters are classified correctly but route to `human_review` with `ADAPTER_NOT_READY`. Unknown HTTPS targets route to `UNSUPPORTED_ATS_HOST`.

LinkedIn is deliberately special: a LinkedIn job URL is not assumed to be Easy Apply. The existing source resolver must explicitly classify it as `linkedin_easy_apply`; external Apply URLs must be resolved to their underlying ATS before adapter dispatch.

## Planned application entry strategy — AWLI

Apply with LinkedIn (AWLI) is a planned application-entry/prefill strategy on the underlying ATS, distinct from LinkedIn Easy Apply and LinkedIn external-apply source resolution. Evaluate it as the primary/fast-path when the exact target offers it, the LinkedIn session is ready, and the route has reviewed live evidence. Keep the native ATS authenticated/guest flow as fallback. Availability is employer/target-specific; detection alone grants no capability or runtime authority.

LinkedIn is never the source-of-truth. Inspect the returned ATS form and verify every AWLI-prefilled field against the canonical candidate profile and approved policy answers. Apply only reviewed normalization/corrections with current safe-fill authority and readback; unresolved mismatches, missing provenance, locked incorrect fields or unknown required answers route to `human_review`. Never import AWLI values into the canonical profile automatically.

Missing/expired LinkedIn sessions, MFA, CAPTCHA/security challenges, unexpected authorization scopes or return-target drift fail closed. No automated credential entry, challenge bypass or retry loop is introduced. Use native fallback only after a fresh inspection proves a clean, reviewed target and no ambiguous application side effects; otherwise stop for `human_review`. Preserve opportunity/application identity and dedupe across route changes.

AWLI accelerates entry/prefill only. Screening, work-right/sponsorship/salary/consent policy, approved CV selection/upload verification, remaining steps, SUBMIT-1, adapter-scoped submit canary, confirmation reconciliation and history remain the underlying ATS adapter's responsibility. AWLI success or a Submit click is never Applied/Confirmed evidence.

This is a documentation-level contract extension, not an implemented dispatch enum or capability promotion. Keep the existing ATS family and L0 `mutation_authorized = false` / `submit_authorized = false` invariants. Future entry evidence should record the selected route, session/auth outcome, sanitized prefill verification result, fallback reason and ATS return-surface fingerprint using existing execution/evidence lineage; do not store raw values, cookies, tokens or authorization codes.

Reference: [ADP AWLI feasibility spike](ADP_AWLI_FEASIBILITY_SPIKE.md).

## Human-review contract

Common reasons include:

- route unresolved;
- unsupported ATS;
- adapter not ready;
- authentication/session/MFA/challenge boundaries;
- ATS drift;
- unknown required control;
- unresolved required answer;
- policy block;
- missing submit confirmation.

Human-review records carry execution/opportunity identity, ATS family, stage, reason, sanitized detail code and evidence reference. They do not authorize writes and they do not store raw candidate field values.

## Safety invariant

Every L0 `AdapterDispatchDecision` has:

- `mutation_authorized = false`
- `submit_authorized = false`

Any attempt to construct a decision that grants either authority fails validation. Adapter-specific live workflows remain the only place where reviewed mutation/submit authority can later be enabled.

## Next

L1 uses this contract to finish ADP end to end and then promote ADP from `live_canary` toward adapter-scoped production readiness after its submit/confirmation evidence passes.
