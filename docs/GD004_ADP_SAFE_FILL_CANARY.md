# GD-004 ADP Identity Safe-Fill Canary

## Purpose

The ADP identity safe-fill canary advances the already reviewed ADP job-detail page through exactly one visible `Apply` entry action, verifies the reviewed guest identity surface, then writes only three AUTO_SAFE identity facts:

- `candidate.first_name` -> `#guestFirstName`
- `candidate.last_name` -> `#guestLastName`
- `candidate.email` -> `#guestEmail`

The phone-country selector and mobile number are observed but deliberately left untouched in this canary.

## Evidence source

The canary was introduced after live navigation run `34968674408` reached `manifest_review_candidate` with:

- one successful Apply navigation click;
- no CAPTCHA;
- no authentication boundary;
- five visible application controls;
- three required identity controls with stable element IDs;
- no visible CV file control yet.

The approved visible-control surface fingerprint is:

`d8b72afea27912bd5e280d8a819ec836e44c2982faa3a3179bbb06c7aa58aa7f`

The pre-navigation surface remains:

`567e7890f5a01f151dbeeb23851ad7cf5fb8100a32c3e0386227d17cd507d313`

## Profile privacy

The workflow reads candidate values from the existing `EJS_LIVE_CANARY_MANIFEST_JSON` GitHub secret. Values are never passed as workflow-dispatch inputs and are never emitted in the artifact. Evidence stores only short SHA-256 value/readback hashes plus boolean validity/readback results.

Only the canonical fields `candidate.first_name`, `candidate.last_name`, and `candidate.email` are read from the profile manifest.

## Fail-closed gates

Before any write, the canary requires:

1. exact immutable source SHA and explicit approval phrase;
2. exact approved pre-navigation surface fingerprint;
3. exactly one normal Apply click for the approved ordinal;
4. no CAPTCHA/auth boundary after navigation;
5. exact approved visible-control surface fingerprint;
6. unique, visible, enabled controls with exact reviewed IDs, labels, types and requiredness.

Any mismatch stops before the first form write.

Every executed write is followed immediately by exact readback and browser validity validation. There is no retry loop.

## Authority boundary

Allowed:

- exactly one normal Apply navigation click;
- up to three AUTO_SAFE `fill()` operations when the current value does not already match;
- readback and structural inspection.

Forbidden:

- credentials;
- phone/country changes;
- select-option changes;
- CV/file upload;
- consent actions;
- CAPTCHA/challenge bypass;
- a second navigation/action click;
- final Submit.

After the three verified identity fields are filled, the canary stops and records non-secret visible button accessibility evidence for the next reviewed step.
