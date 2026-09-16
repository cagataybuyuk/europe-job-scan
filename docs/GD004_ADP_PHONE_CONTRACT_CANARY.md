# GD-004 ADP Phone Contract Canary

## Purpose

Live diagnostic run `34989901584` proved that ADP blocks Continue with `Mobile Number is required.` even though the visible `phoneCountry` select and `login_view_phone` tel input expose native `required=false`.

Before any phone value is written, this canary inspects the exact live phone-control contract.

## Authority boundary

The only mutations are the already reviewed navigation/privacy sequence:

1. open OneTrust preferences;
2. click `Unselect All`;
3. click `Save Changes`;
4. click one reviewed `Apply` action.

After Apply, inspection is read-only.

The canary never:

- writes first name, last name, email, country, or phone;
- selects a phone country;
- clicks Continue;
- enters credentials or social sign-in;
- uploads a CV;
- bypasses CAPTCHA;
- submits an application.

## Evidence captured

The artifact records only non-secret UI contract evidence:

- exact country-select name and native requiredness;
- selected country value/label;
- country option labels/values and disabled state;
- phone input id/name/type;
- `required`, `aria-required`, `pattern`, `inputmode`, `minlength`, `maxlength`, `placeholder`, `autocomplete`, and `aria-describedby`;
- visible mobile-number instruction text;
- the previously observed runtime-required fact.

No candidate profile secret is loaded by this workflow.

## Workflow

`.github/workflows/gd004-adp-phone-contract-canary.yml`

Environment: `gd004-safe-fill-upload-canary`

Approval phrase:

`APPROVE-TEST-ADP-PHONE-CONTRACT-INSPECTION`

## Next decision

After live inspection:

1. determine how the canonical phone value must be split into country + local number;
2. add a narrow phone-format contract;
3. require a user-grounded phone source and explicit ADP-only name-transliteration policy;
4. only then consider a new identity safe-fill canary that writes reviewed ASCII names and phone.

CV upload and final Submit remain disabled.
