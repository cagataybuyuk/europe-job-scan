# Reviewed native entry and draft profile correction

USER-NATIVE-MVP-20261112 prefers the reviewed employer-native form using verified
CV/profile facts. LinkedIn is optional when its permission/return path is
verified. An AWLI click never authorizes blind native fallback.

New MVP command executions require this exact independently pinned manifest entry:

```json
{"entry_policy":{"strategy":"native_verified_facts","decision_ref":"USER-NATIVE-MVP-20261112"}}
```

The compiler validates it; the command checks the occupied canonical budget,
requires this policy, then uses `reviewed_oneclick_boundary`. It never injects a
strategy into reviewed inputs. Legacy library callers may omit the policy;
validation-only mode opens no browser.

Native entry does not click AWLI. Only the strict public widget URL/origin shape
and the same owned iframe are accepted. Widget document GET navigation is allowed
only for the two canonical public base URLs already in the reviewed `read_urls`.
No general LinkedIn/OAuth navigation, credentials or POST permission is added.
Unknown/auth/challenge frames and changed policy stop; faults are never reset.

## Source-bound corrections

`steps[0].reviewed_profile_corrections` binds the complete container/card inventory,
each existing card identity and observed text, exact Edit action, editor selector
and fingerprint, complete labeled controls, verified source values and one Save.
Every card also has a source-backed full-summary assertion. Unedited cards must
remain unchanged; an index alone is not identity. Ordinary field bindings do not
become a general profile writer.

Values match approved fact type/value/provenance. Current-employment checkboxes
require explicit verified current facts. Supported dates have verified YYYY-MM
values. Native month inputs and the separately reviewed SPL month picker below
are supported. Unknown optional months may be blank only with the
declared month-unknown omission policy and verified empty fact. Required missing
dates, day-level controls, unknown records and unsupported editors stop. No dates
or current job are inferred from LinkedIn prefills.

Corrections run on the owned entry page before ordinary fills/CV. Only an exact
reviewed pending card may defer its final summary during return acceptance. After
Save, all final assertions and untouched cards are rechecked. Save stays in read
phase: POST/PUT/PATCH/DELETE remain denied. A denied Save write stops and cannot
be retried by clearing the fault. Delete/unknown confirmation actions are absent.

Native inspection rejects uncovered visible experience/education Edit cards,
including cards created by the CV parser, before submission intent/application POST.

## Reviewed SPL controls

The logical date remains a month. A `month_picker` binding declares
`type: spl-month-year-v1`, the exact `field_selector`, `picker_selector`,
`calendar_selector`, independently reviewed `calendar_fingerprint` and
`display_format: iso-month-start-transport-v1`. Only one owned `spl-date-field`
and `spl-date-picker`, both `type=month-year`, may contain the From/To text input.
The calendar must have its reviewed Year control, previous/next-year actions and
twelve unique month cells. Year navigation is bounded; unknown controls,
duplicate cells, changed ownership or changed fingerprints stop.

The widget transports `YYYY-MM-01`; readback exposes logical `YYYY-MM`. The day
does not become a candidate employment fact. Clearing an existing optional month
also requires an exact reviewed Clear date action and the verified omission
policy. A missing required education/employment month remains a blocker.

Title/Company/Office controls may declare
`text_autocomplete: {type: spl-profile-text-v1, host_selector: <exact host>}`.
Only the reviewed OC/SPL ownership chain is accepted. Fill, blur and readback use
the verified text; no suggestion is selected or promoted to fact.

The ordinary first-page City field has a separate
`city_autocomplete: {type: spl-city-text-v1, host_selector: <exact OC-LOCATION>}`
binding. It requires `canonical_field: candidate.city`, `kind: text`, label City
and Verified Fact resolution. It does not turn arbitrary comboboxes into text
inputs or activate profile editing. Unknown visible controls and denied network
requests still stop the flow.

The phone Country code button needs an independent
`phone_country_select` binding: `type: spl-phone-country-v1`, exact SPL select
`host_selector`, `country_iso2`, `country_label`, `option_label` and
`country_provenance_ref`. The logical `candidate.phone_country_code` remains a
dial code such as `+90`; a separate verified `candidate.phone_country` fact must
match the reviewed country label and source. These are synthetic format examples,
not authorization to use any candidate fact.

The button's generic DOM value may be empty despite a visible country prefix.
Only this explicit binding can read the owned SPL select's value, selected
option, assigned visible trigger prefix and country flag together. Their ISO,
label and dial code must agree. Selection uses the uniquely reviewed option in
that button's associated menu. Unknown ownership, multiple selections, a stale
prefix or any mismatch stops; generic combobox readback stays unchanged.

## Evidence and limits

Actual Chromium tests fulfill synthetic HTTPS locally and cover source-backed
edits, native entry, month omission, changed cards/controls, denied Save requests,
and the full correction-to-CV-to-single-confirmed-fixture flow. Existing
controller/ledger tests remain in CI. Fixtures are not employer receipts.

Actual employer editor/Save compatibility remains unproven. No new live
application, OAuth grant, paid API, deployment or PROD release occurred. The first
real application remains Confirmed/consumed; its existing Applied Sheet result
was manually reconciled on 2026-11-11. The later employer rejection is preserved
in the central record; a confirmed submission does not imply hiring success.
