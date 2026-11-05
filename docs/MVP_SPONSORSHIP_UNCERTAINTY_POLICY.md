# First-application sponsorship uncertainty

The user's 2026-11-05 decision permits the first suitable application when the
employer does not state sponsorship support. This changes vacancy selection;
it is not evidence that an employer sponsors applicants. Answer candidate work
authorization No and sponsorship required Yes from independently verified facts.
An explicit sponsorship exclusion or an unmet mandatory existing-rights condition
still blocks the target.

`evaluate_vacancy_evidence` remains strict by default. A caller may supply a
`SponsorshipReviewPolicy` bound to the exact employer posting URL, an opaque
reference to the actual user decision, and first-application-only scope. UNKNOWN
is accepted for review only when all existing freshness, employer-source,
role-fit and mandatory-requirement checks pass, plus all four explicit facts:

- Candidate has existing work rights: False.
- Candidate needs sponsorship: True.
- Checked vacancy explicitly excludes sponsorship: False.
- Checked vacancy requires existing work rights: False.

Missing or malformed facts do not imply False. Stated unsupported sponsorship
cannot use the exception. The result keeps `sponsorship_status=UNKNOWN`; all
execution, write, upload and submit authority flags stay False. The caller must
verify the decision reference and underlying facts independently: this pure
classifier authenticates neither a user decision nor an employer source.

Freshness windows accept literal integer hours from 1 through 48. A caller can
shorten the window, never extend it. Nonfinite values, booleans and other types
are rejected before evidence classification.

The selected live target for this decision is Version 1 Senior Business Analyst
REF7089X, posting 744000159638089, Dublin. This document does not pin a mutable
live eligibility result. Recheck the actual vacancy and form before action.
CV/answer approval, exact-target and endpoint review, independent review,
CAPTCHA/new OAuth gates, durable single-application protection and employer
confirmation remain required. No paid API or multi-application rollout is added.

Local verification: 110 focused tests passed with zero skips, including exact
target binding, truthful candidate facts, exclusion checks, default denial,
malformed-policy inputs and stale/nonfinite freshness regressions. Independent
review repeated the suite and found no remaining confirmed finding. These tests
do not prove a live application was uploaded, advanced or submitted.
