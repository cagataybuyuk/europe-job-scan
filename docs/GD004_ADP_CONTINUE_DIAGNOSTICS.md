# GD-004 ADP Post-Continue Diagnostics

## Purpose

The reviewed ADP Continue canary proved that the cookie-preference flow, one Apply click, three identity writes, and one Continue click can execute safely. In live run `34988265197`, however, the page remained on the same visible identity-control surface after Continue. This diagnostic canary exists only to determine why the stage did not visibly advance.

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

## Expected next decision

- Validation/error evidence -> review the exact blocking control or message before any new mutation authority.
- Same identity surface with no error evidence -> inspect ADP custom-component/state behavior; do not blindly retry Continue.
- New custom/form surface -> build a reviewed manifest from the captured structure before any safe-fill or upload authority is added.

Final Submit remains disabled throughout.
