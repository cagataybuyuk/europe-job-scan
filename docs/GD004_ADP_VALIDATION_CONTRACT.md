# GD-004 ADP Validation Contract

## Evidence

Live diagnostic run `34989901584` proved that the reviewed identity values passed native browser readback and `checkValidity()`, but ADP's own custom validation layer then blocked Continue with:

- `Invalid First Name.`
- `Invalid Last Name.`
- `Mobile Number is required.`

The same live artifact showed `guestFirstName`, `guestLastName`, and `guestEmail` as native required controls, while `phoneCountry` and `login_view_phone` exposed `required=false`. Therefore Mobile Number is a runtime ADP requirement and must not be inferred from native HTML requiredness alone.

ADP Applicant Onboard documentation independently states that first and last names accept only `A-Z`, `a-z`, space, parentheses, hyphen, and apostrophe; the first character must be alphabetic; identical special characters must not repeat consecutively.

## Contract

`src/ejs/services/adp_validation_contract.py` classifies canonical candidate profile values without browser interaction.

### Name contract

`adp-ascii-name-v1`

- exact documented ASCII grammar;
- no silent whitespace cleanup;
- repeated identical special characters fail closed;
- non-compatible values are marked for user profile-policy review.

A candidate-only Turkish-to-ASCII transliteration function is implemented for future reviewed use:

`Ç/ç, Ğ/ğ, İ/ı, Ö/ö, Ş/ş, Ü/ü -> ASCII equivalents`

It is **not authorized for browser writes** by this contract. The report contains only hashes and compatibility booleans, never raw profile values or raw transliteration candidates.

### Phone contract

`adp-runtime-phone-required-v1`

- Mobile Number is treated as required because live ADP validation proved it.
- Native HTML `required=false` is not trusted for this field.
- Phone presence may be classified from a canonical profile.
- Phone formatting, country splitting, and control-write rules remain unresolved until separately reviewed.

## Authority

This contract has classification-only authority:

- browser write: **OFF**
- Continue click: **OFF**
- file upload: **OFF**
- final Submit: **OFF**

No Playwright/browser/network mutation is present in the module.

## Next gate

Before ADP identity execution can advance, two user-grounded decisions are required:

1. approve an ADP-only ASCII transliteration policy for names when the canonical name is not ADP-compatible;
2. provide or confirm the canonical mobile number source, after which a separate read-only phone-format/control contract must be reviewed before any phone write is enabled.

Final Submit remains disabled.
