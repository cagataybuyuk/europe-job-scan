# GD-004 ADP Profile Policy v2

## Evidence basis

Live ADP run `35067757870` inspected the phone controls without loading or writing candidate profile values. It observed:

- country selector: `select[name=phoneCountry]`;
- 245 ISO-2 country options, including `TR` for Turkey;
- default selected country: `US`;
- phone input: `#login_view_phone`, `name=phone`, `type=tel`, `autocomplete=tel`;
- no native `pattern`, `minlength`, or `maxlength` contract;
- instruction text: `Country code was added to the field.`;
- prior live Continue diagnostic: `Mobile Number is required.`

The earlier validation contract also established that ADP first/last names accept the documented ASCII-oriented grammar. A deterministic Turkish-to-ASCII candidate transformation exists but is not authorized for browser writes without explicit user approval.

## Secret composition

The existing environment-scoped `EJS_ADP_CANARY_PROFILE_JSON` remains the source for the already configured identity facts:

- `first_name`
- `last_name`
- `email`

A separate environment-scoped secret, `EJS_ADP_CANARY_PROFILE_V2_EXTENSION_JSON`, carries only the new phone/policy facts:

```json
{
  "phone_country_iso2": "<private ISO-2>",
  "phone_national_number": "<private digits only>",
  "adp_ascii_name_policy_approved": true
}
```

The live workflow merges the two secrets into an ephemeral in-run profile, validates it, then removes the temporary files. This avoids forcing the user to re-enter the existing identity secret.

## Name policy

- If a supplied name already satisfies the ADP name contract, it is used unchanged.
- If it does not, the deterministic Turkish-to-ASCII candidate can be used only when `adp_ascii_name_policy_approved` is exactly `true`.
- The transformed value must independently satisfy the ADP name contract.
- Outer whitespace, repeated invalid punctuation, or an invalid post-transliteration result fail closed.
- Raw and transformed name values are never written to artifacts or logs; only hashes and booleans may be emitted.

## Phone policy

- `phone_country_iso2` must be exactly two uppercase ASCII letters.
- `phone_national_number` must be digits only and is treated as an exact user-supplied fact.
- The system does not infer country, strip a country code, remove a leading zero, or otherwise semantically rewrite the phone number.
- At runtime, the reviewed country selector must contain exactly one enabled option matching the supplied ISO-2 value before selection is allowed.
- The phone input must match the reviewed structural contract before the national number is written.
- Raw phone values are never written to artifacts or logs; only a value hash, digit count, and country ISO-2 may be emitted.

## Reviewed phone fingerprint

The structural fingerprint derived from live run `35067757870`, excluding transient selected-option state while preserving the complete country-option set and phone-control contract, is:

`d6f3dc96678e66f5785c39ffc8bd4d6451204b69b2afbdf08493a6596727305c`

The profile-v2 canary must match this fingerprint before selecting a country or writing a phone value.

## Live canary authority

Workflow: `.github/workflows/gd004-adp-profile-v2-continue-canary.yml`

Environment: `gd004-safe-fill-upload-canary`

Approval phrase:

`APPROVE-TEST-ADP-PROFILE-V2-PHONE-AND-CONTINUE`

The reviewed profile-v2 continuation canary may perform only this sequence:

1. open OneTrust preferences;
2. `Unselect All`;
3. `Save Changes`;
4. one reviewed `Apply` click;
5. fill first name, last name, email;
6. select one reviewed phone-country option;
7. fill one reviewed phone input;
8. verify exact readback and the reviewed Continue action surface;
9. click exactly one `Continue`;
10. inspect the resulting application surface read-only.

Maximum authority is five reviewed click paths and five profile writes. No credentials, social sign-in, CV upload, further application action, CAPTCHA bypass, or Final Submit is authorized.

## Required user-controlled release input

Before the live profile-v2 canary is dispatched, the user only needs to create/update `EJS_ADP_CANARY_PROFILE_V2_EXTENSION_JSON` with:

1. `phone_country_iso2`;
2. exact digits-only `phone_national_number`;
3. explicit boolean `adp_ascii_name_policy_approved`.

If the extension secret is missing, malformed, or does not contain exactly those three keys, execution fails before any browser profile write.
