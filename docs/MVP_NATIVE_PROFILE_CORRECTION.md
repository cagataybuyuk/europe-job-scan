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
require explicit verified current facts. Supported dates are native month inputs
with verified YYYY-MM values. Unknown optional months may be blank only with the
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

## Evidence and limits

Actual Chromium tests fulfill synthetic HTTPS locally and cover source-backed
edits, native entry, month omission, changed cards/controls, denied Save requests,
and the full correction-to-CV-to-single-confirmed-fixture flow. Existing
controller/ledger tests remain in CI. Fixtures are not employer receipts.

Actual employer editor/Save compatibility remains unproven. No new live
application, OAuth grant, paid API, deployment or PROD release occurred. The first
real application remains Confirmed/consumed; its existing Applied Sheet result
was manually reconciled on 2026-11-11.
