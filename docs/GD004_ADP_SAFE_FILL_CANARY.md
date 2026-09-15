# GD-004 ADP Identity Safe-Fill Canary

## Purpose

The ADP identity safe-fill canary advances the already reviewed ADP job-detail page through one visible `Apply` entry action, verifies the reviewed guest identity surface, then writes only three AUTO_SAFE identity facts:

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

Live safe-fill run `34971068117` then exposed a separate browser-boundary issue before any application write:

- the dedicated ADP profile secret materialized successfully;
- the reviewed navigation surface still matched;
- a visible OneTrust privacy banner exposed `Set your preferences`, `Deny`, and `Agree and proceed` actions;
- the normal Apply click timed out while the banner was present;
- Apply success count remained `0`;
- identity write count remained `0`;
- upload and submit remained `0`.

The runtime therefore treats this as a cookie-preference boundary, not as permission to force-click the application action.

## Profile privacy

The workflow reads candidate values only from the dedicated `EJS_ADP_CANARY_PROFILE_JSON` GitHub secret. This secret is intentionally separate from the broader live canary manifest/CV configuration so ADP identity writes do not depend on unrelated ATS or upload metadata.

Expected secret shape:

```json
{"first_name":"...","last_name":"...","email":"...","profile_version":"adp-canary-profile-v1"}
```

`profile_version` is optional. Candidate values are never passed as workflow-dispatch inputs and are never emitted in the artifact. The workflow materializes a temporary canonical profile file, deletes the raw secret-derived JSON before browser execution, and evidence stores only short SHA-256 value/readback hashes plus boolean validity/readback results.

Only `candidate.first_name`, `candidate.last_name`, and `candidate.email` are made available to the safe-fill runtime.

## Cookie-boundary policy

The default `cookie_policy` is `block`. If a visible cookie banner is present, the canary stops before the Apply click.

A separately approved `deny_optional` policy may act only when the exact OneTrust boundary is present:

- banner selector: `#onetrust-banner-sdk`;
- deny selector: `#onetrust-reject-all-handler`;
- deny label: `Deny`;
- accept selector: `#onetrust-accept-btn-handler`;
- observed accept label must remain `Agree and proceed` so the surface is known, but that control is never clicked.

The `deny_optional` path requires the exact workflow approval phrase:

`APPROVE-TEST-ADP-DENY-OPTIONAL-COOKIES-AND-IDENTITY-SAFE-FILL`

It permits at most one cookie-preference click, and that click can only target the exact OneTrust reject-all control. If the OneTrust structure or labels drift, execution stops before Apply.

## Fail-closed gates

Before any identity write, the canary requires:

1. exact immutable source SHA and the approval phrase corresponding to the selected cookie policy;
2. non-empty dedicated ADP identity profile secret with first name, last name and a minimally valid email;
3. exact approved pre-navigation surface fingerprint;
4. either no visible cookie banner, or the separately approved exact OneTrust deny-only boundary;
5. at most one normal Apply click for the approved ordinal;
6. no CAPTCHA/auth boundary after navigation;
7. exact approved visible-control surface fingerprint;
8. unique, visible, enabled controls with exact reviewed IDs, labels, types and requiredness.

Any mismatch stops before the first form write. Every executed identity write is followed immediately by exact readback and browser validity validation. There is no retry loop and no force-click path.

## Authority boundary

Allowed only with the matching explicit approval:

- optional: at most one exact OneTrust `Deny` click for non-essential cookies;
- exactly one normal Apply navigation click;
- up to three AUTO_SAFE `fill()` operations when the current value does not already match;
- readback and structural inspection.

Forbidden:

- accepting optional cookies / clicking `Agree and proceed`;
- application consent actions;
- credentials;
- phone/country changes;
- select-option changes;
- CV/file upload;
- CAPTCHA/challenge bypass;
- another application navigation/action click;
- final Submit;
- `force=True` or click retry loops.

After the three verified identity fields are filled, the canary stops and records non-secret visible button accessibility evidence for the next reviewed step.
