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


## Live profile-v2 result — 2026-09-22

Run: `35712126399`

The previously observed OneTrust timeout did not recur. The bounded sequence reached and completed:

- preference-center navigation: `1/1`;
- `Unselect All`: `1/1`;
- `Save Changes`: `1/1`;
- reviewed `Apply`: `1/1`;
- profile writes: `5/5` attempted/succeeded at the Playwright operation level;
- credentials: `0`;
- upload: `0`;
- submit: `0`.

Profile materialization also passed: the reviewed Turkish-to-ASCII policy produced ADP-compatible name evidence, email validation passed, and the observed phone structural fingerprint exactly matched the reviewed contract.

The run stopped **before Continue** with `ADP_PROFILE_V2_WRITE_FAILED:PermissionError`. Given the counters and code ordering, all three identity fields and the country select had already passed their exact readback checks and the phone fill operation had completed. The remaining PermissionError branch is the phone readback mismatch invariant. Therefore the current blocker has narrowed to **ADP phone-value normalization/readback**, not cookie navigation, Apply navigation, identity transliteration, phone control discovery, upload or submit.

The v3 diagnostic patch records only sanitized phone readback shape on mismatch (digit counts, suffix-equivalence, TR calling-code prefix, trunk-zero prefix and formatting presence). It never persists the raw phone value. Continue remains blocked until this normalization behavior is evidenced and reviewed.


## Live opener recurrence — run 35716218979

The v3 phone-readback diagnostic did not reach the phone stage because the legacy OneTrust opener path timed out again on its first click. The artifact proved that the application-entry fingerprint was unchanged and that `#onetrust-pc-btn-handler` was visible and enabled on the banner, while the older `#ot-sdk-btn` control also remained in the DOM.

This explains why the earlier text-based opener was intermittent: it was bound to the legacy control rather than the banner CTA. The opener is now constrained to the exact reviewed ID `#onetrust-pc-btn-handler` with only the two observed accessible-label variants accepted. No force-click or retry authority was added.

The phone-readback normalization question therefore remains open; the next live profile-v2 run must first prove the deterministic reviewed OneTrust opener and then, if it reaches the phone mismatch again, capture the existing sanitized phone shape evidence.


## Live phone normalization evidence — run 35719546694

The reviewed OneTrust opener revision succeeded. The canary completed preference-center navigation, Unselect All, Save Changes, Apply and all five profile writes, then stopped before Continue on the explicit phone readback invariant.

Sanitized readback evidence proved the transformation:

- expected national digit count: `10`;
- observed readback digit count: `12`;
- expected national number is an exact suffix of the observed digits;
- the only added prefix is Turkey's calling code `90`;
- no trunk-zero prefix was added;
- presentation formatting contains non-digit separators;
- raw phone value was not persisted.

Therefore ADP's live phone control semantically preserves the reviewed national number while rendering/storing it as `90 + national number` with display formatting after country `TR` is selected.

The profile-v2 readback contract is revised narrowly for this evidenced behavior:

- accept digit-equivalent national readback;
- for `TR` only, also accept exact `90 + national number` digit normalization;
- continue to reject trunk-zero insertion, another country calling code, digit drift or other transformations;
- do not expose the raw phone value.

This changes validation semantics only. It does not add writes, clicks, upload authority or Submit authority.
