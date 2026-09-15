# GD-004 ADP Post-Continue Diagnostics

## Purpose

The reviewed ADP Continue canary proved that the cookie-preference flow, one Apply click, three identity writes, and one Continue click can execute safely. In live run `34988265197`, however, the page remained on the same visible identity-control surface after Continue. The dedicated diagnostic run `34989901584` was therefore used to determine why the stage did not visibly advance without expanding mutation authority.

## Authority boundary

The mutation authority is identical to the already reviewed path and does not expand it:

1. open OneTrust preference center;
2. click `Unselect All`;
3. click `Save Changes`;
4. click one reviewed `Apply` action;
5. fill only `candidate.first_name`, `candidate.last_name`, and `candidate.email` with exact readback;
6. verify the reviewed post-fill action fingerprint;
7. click exactly one reviewed `Continue` action.

After that Continue click the canary is read-only.

It never enters credentials, uses social sign-in, changes phone/country, uploads a file, accepts optional cookies, clicks another application action, bypasses CAPTCHA, or submits an application.

## Live diagnostic result — 2026-09-15

Run: `34989901584`

Result: `canary_status=diagnosed`

The reviewed mutation sequence remained within bounds:

- preference-center navigation: `1/1`;
- `Unselect All`: `1/1`;
- `Save Changes`: `1/1`;
- reviewed `Apply`: `1/1`;
- identity writes: `3` with exact readback and browser validity success;
- reviewed `Continue`: `1/1`;
- file uploads: `0`;
- final submits: `0`.

After Continue, the same five visible identity controls remained on screen. The URL did not advance to a new application stage. No CAPTCHA or authentication boundary was observed.

The ADP custom validation layer emitted the following blocking evidence:

- `Invalid First Name.`
- `Invalid Last Name.`
- `Mobile Number is required.`

This is materially different from native browser validation: the three reviewed identity writes passed exact readback and `checkValidity()`, while ADP's own custom validation rejected the name fields. The mobile field had previously appeared optional in the structural inspector (`required=false`) but ADP's runtime validation treats it as required.

Therefore the current blocker is **ADP-specific field validation**, not navigation or cookie handling.

## Read-only evidence

The artifact captures non-secret structural evidence only:

- whether the reviewed identity surface persists;
- visible application-control contracts without values;
- visible button labels;
- visible `role=alert` / `aria-live` evidence;
- visible error-like text;
- visible invalid controls and browser validation messages;
- visible custom-element contracts across open shadow roots;
- focused element metadata.

Candidate first name, last name, email, generic email patterns, and phone-like strings are redacted from diagnostic text before artifact creation.

## Workflow

Workflow: `.github/workflows/gd004-adp-continue-diagnostic-canary.yml`

Environment: `gd004-safe-fill-upload-canary`

Approval phrase:

`APPROVE-TEST-ADP-CONTINUE-DIAGNOSTICS`

The existing environment-scoped `EJS_ADP_CANARY_PROFILE_JSON` secret is reused; candidate values are not workflow inputs or artifact fields.

## Next decision

Do not retry Continue and do not widen upload/submit authority yet.

The next development step is a **reviewed ADP validation-contract investigation** focused on:

1. determining the exact accepted name contract exposed by ADP custom components/events without logging candidate values;
2. determining why ADP runtime validation requires Mobile Number despite the native structural contract reporting it optional;
3. deciding whether a user-approved name normalization/transliteration policy is needed rather than silently changing candidate identity data;
4. only after evidence, extending the dedicated profile contract to phone country/mobile if those fields are proven required and explicitly approved for safe-fill.

The application must not proceed to CV upload or later form stages until this identity/phone validation gate passes.

Final Submit remains disabled throughout.
