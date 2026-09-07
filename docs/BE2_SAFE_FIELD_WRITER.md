# BE-2 Safe Field Writer

Run: `BE2-20260821-2212`
Service version: `0.9.0a1`
Contract: `PREFILL-EXEC-0.2`

## Scope

BE-2 upgrades browser execution from read-only inspection to bounded non-sensitive pre-fill. The worker accepts only `To Apply` opportunities with High readiness, zero unresolved required controls, a current inspected form fingerprint and an explicitly provenance-backed field plan.

## Write authority

Allowed in this wave:
- verified AUTO_SAFE identity/contact/profile fields on the explicit canonical allowlist;
- exact select values where the observed option text matches;
- approved DYNAMIC_AI motivation text when resolver status is Resolved/Optional Resolved and no review flag is present.

Still structurally forbidden:
- salary and review-gated declarations;
- work authorization / sponsorship;
- consent / legal / protected / demographic actions;
- exact experience-year or exact-date declarations;
- credentials / account creation;
- file upload;
- CAPTCHA bypass;
- final Submit.

## Safety mechanics

1. The entire field plan is validated before the first DOM write.
2. The live form fingerprint must equal the inspected fingerprint.
3. Every targeted control must still exist, have the expected type and a stable id/name locator. Observed constraints such as `maxlength` are enforced before the first write.
4. Select controls require an exact observed option.
5. Each write is immediately read back and compared exactly.
6. A pre-existing exact value is an idempotent no-op (`already_matched`).
7. Returned/audited field results contain value hashes only, not raw candidate values.
8. The form fingerprint is recomputed after all writes; structural drift prevents a clean review-gate result.
9. The worker has no upload or submit methods.

## Canary

Local Chromium fixture:
- 12 observed controls / 6 required controls;
- 7 planned safe fields;
- 7/7 written and exact-readback verified;
- form fingerprint remained stable: `dd92920e17ed0edd535b8855`;
- 0 file uploads;
- 0 submit clicks;
- review gate reached.

Idempotency fixture:
- 2 exact pre-existing values;
- 0 DOM writes;
- 2/2 `already_matched`.

Drift fixture:
- stale expected fingerprint;
- `schema_drift`;
- 0 DOM writes / 0 submit clicks.

Regression: `163/163 PASS`.

## Live production gate

No live employer form write was attempted. BE-1B still lacks outbound Chromium egress in the current host, so no trustworthy live form fingerprint is available. Production pre-fill remains fail-closed until the worker is bound to an outbound-enabled runtime.
