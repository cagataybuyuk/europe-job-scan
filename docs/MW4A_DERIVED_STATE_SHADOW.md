# MW-4A Derived State Shadow / Parity

Date: 2026-08-17
Run: `MW4A-20260817-1529`
Service version: `0.4.0a1`

## Scope

MW-4A is shadow/parity only. It derives application readiness and readiness-driven User Action projections without mutating production derived state.

Inputs used by the first bounded live shadow:
- active `To Apply` Form Fill Queue records;
- current readiness confidence / blocking-review metadata;
- CV upload asset readiness;
- current User Action Center projection for comparison.

## Acceptance result

- Form Fill Queue status parity: 12/12.
- User Action inclusion parity: 12/12.
- User Action category/priority/batch parity: 12/12.
- Critical divergence: 0.
- Business-state mutations: 0.
- Production derived-state mutations: 0.
- External-form mutations: 0.
- Unit/safety suite: 36/36.

## Fail-closed behavior

Patagonia remains audit-only because readiness confidence is Medium. Needs Form Access rows also remain outside the initial MW-4B production write scope even though their shadow status matches.

Initial MW-4B candidate set is intentionally bounded to four High-confidence readiness records:
- Planon — Business Analyst
- Rabobank — Business Analyst (IGA4NHI)
- Karsten International — Business Analyst & Product Owner
- Kirby Group Engineering — Business, Process & Systems Analyst

MW-4B must retain zero authority over Applications, pipeline state, user facts, external form filling, consent, CAPTCHA, work-right/legal/protected choices, file upload and final submit.
