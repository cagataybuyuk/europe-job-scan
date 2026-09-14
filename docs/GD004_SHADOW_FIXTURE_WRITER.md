# Scoped Shadow DOM prefill: offline fixture stage

This stage extends the read-only inspector with **synthetic text-field writes
only**. It is not connected to the live canary executor and does not change
PR #16 compatibility blockers, environment approvals, or final-submit flags.

## Entry point and authority

`ejs.services.shadow_fixture_writer.run_shadow_fixture(plans)` opens its own
fresh browser/context, sets offline mode, blocks HTTP routes and service
workers, and loads only bundled synthetic HTML. It accepts no URL, uploaded
HTML, CV, or live manifest. Use synthetic values, never actual candidate data.
The private page helper is an implementation detail, not a live writer API.

Plans reuse the existing prefill policy: provenance is required; only allowed
canonical fields with resolved, high-confidence mappings and no review flag
are eligible. This prototype supports text/email/tel/url/textarea only.
File fields, credentials, consent, comboboxes and other custom inputs are not
supported. No click, Next, upload, or submit operation is implemented.

## Verification rules

- Explicit host-ID chains plus a native input ID and expected label scope
  each target. IDs accept a restrictive identifier syntax, not arbitrary CSS.
- Every host/field locator must match exactly once. There is no `.first()`.
- All plans pass preflight before writing begins: type, label, availability,
  maximum length and scope must match.
- Schema fingerprint is checked before each write and after the final write.
- Values are read back exactly and browser validity is checked. A mismatch
  stops remaining writes; attempted writes remain counted. No rollback is claimed.
- Repeated execution with already-matching values does not rewrite them.
- Results include counts/status only, not field values or raw browser errors.

`fixture_prefill_verified` means only the planned synthetic fields passed
readback. It does not mean every required field is filled, a wizard is complete,
or the form is ready to submit. `live_execution_ready` remains false.

## Tests

`python -m unittest discover -s tests/unit -p test_shadow_fixture_writer.py -v`

The dedicated `gd004-shadow-writer-regression.yml` runs real Chromium tests
without secrets or employer URLs. Coverage includes nested hosts, identical
IDs in separate roots, untouched unselected fields, replay, full-plan preflight,
duplicates, changed schemas, unavailable/custom controls, invalid email,
script-modified input values, no CV attachment, no Next and no submit events.

## Remaining before live use

Review stable selectors against a fresh employer form; integrate explicit
target/schema/manifest approval; support exact autocomplete option selection;
implement per-step validation and separately reviewed wizard transitions;
verify section-scoped CV attachment. A green fixture is not live evidence and
must not release R2. Existing live canary remains blocked on unsupported forms.
