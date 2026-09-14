# GD-004 Shadow DOM adapter: inspection stage

## Scope

`ejs.services.smartrecruiters_shadow.inspect_shadow_form(page)` reads the
currently rendered page and nested **open** shadow roots. It never navigates,
fills, uploads, clicks Next, or submits. Existing live compatibility blockers
from PR #16 remain unchanged. This module is not wired into the live writer.

This stage responds to the Version 1 form observation: native fields sit
inside shadow roots, and both the profile-autocomplete and resume components
contain an input named `file-input`. An unscoped `.first` locator is unsafe.

## Output contract

- `scope` and `observation_key` distinguish controls in different shadow roots.
- Labels are resolved in the control's own root (including label and ARIA references).
- Native required/ARIA/label markers are recorded. Host required attributes
  are separate hints, not proof that an inner control is mandatory.
- Visibility, disabled state, file acceptance and multiplicity are captured.
- `unscoped_locator_collisions` reports reused IDs/names; it does not select a file target.
- A schema fingerprint is generated without native field values or selected files.
- `inspection_only=true`, `live_execution_ready=false`, and zero write/upload/submit
  attempts are explicit. These counters describe this read-only function, not a live canary.

Scope paths use DOM positions and are observation identities, **not approved
write selectors**. Structure changes invalidate the fingerprint. Labels/IDs
are website metadata and may contain site-provided text: do not treat the
report as universally PII-free or publish live reports without review.

Closed shadow roots, iframe contents, custom validation, and subsequent wizard
steps are not certified by this inspector. Absence of a control is not evidence
that it is optional. Host labels and required markers need explicit adapter
support before mutation. The Next flag is diagnostic only.

## Verification

Run `python -m unittest discover -s tests/unit -p test_smartrecruiters_shadow.py -v`.
Real-browser tests require `python -m playwright install chromium` and are run
by `gd004-shadow-adapter-regression.yml` with a synthetic, in-memory page.
They verify nested roots, duplicate file IDs, local labels, hidden/disabled
controls, required hints, stable repeated reads, unchanged input values,
empty file controls and zero UI events. No live employer URL or CV is used.

## Remaining work before live canary

1. Integrate a bounded, explicitly read-only target inspection entry point.
2. Verify stable, section-scoped selectors against a fresh approved live form.
   Distinguish Resume from profile-autocomplete and profile-image uploads.
3. Add city autocomplete and country-code controls with exact option readback.
4. Implement reviewed wizard transitions with per-step schema/required-field
   validation; never infer that Next is harmless or that the last step is reached.
5. Bind manifest identity, profile facts, CV hash and source SHA consistently
   across inspection and execution. Do not promote `To Review` jobs to `To Apply`.
6. Run synthetic write/upload tests, then an explicitly approved live canary.

The existing environment approval and final-submit gates remain required.
This stage does not authorize bypassing them or prove independent R2 review.
