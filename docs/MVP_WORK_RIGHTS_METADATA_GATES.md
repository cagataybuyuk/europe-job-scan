# Country-specific work-rights metadata checks

These three pure helpers classify supplied metadata only:

- `evaluate_work_rights`: exact country, claimed source, verified/current flags.
- `evaluate_bound_work_rights`: candidate/employer/vacancy identity and expiry.
- `evaluate_application_action_preflight`: exact action consent and URL checks.

Every result keeps write, upload and submit authority false. The action preflight
also keeps network authority false, including when its status is PREFLIGHT_READY.
No source is authenticated, no browser or transport is called, and no live ledger
is accessed. The helpers are not connected to the application runtime.

Country strings are compared exactly. Ordinary internal ASCII spaces are allowed
for multiword names; leading/trailing padding, control characters, invisible
format characters and other Unicode separators are rejected. Callers should use
one reviewed country representation consistently. Time-bound helpers require
genuinely aware datetimes, not merely a non-null tzinfo object, and reject invalid
offsets or expired evidence without exposing supplied values in diagnostics.

Verified existing work rights are distinct from eligibility to apply for a role
that may provide sponsorship. These metadata statuses do not replace the vacancy
review or the user's [UNKNOWN sponsorship selection policy](MVP_SPONSORSHIP_UNCERTAINTY_POLICY.md).
Integrating the action preflight as a universal application-eligibility gate would
require a separate policy review; this change does not perform that integration.
The first live application scope remains consumed, with no new submission right.

The three corresponding test files are explicitly listed in core-regression CI.
Tests use synthetic metadata only and perform no employer or browser action.
