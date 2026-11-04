# EJS single-application completion audit — 2026-11-04

## Outcome and scope

There is no verified live end-to-end application MVP. PR #87 passed all ten
checks on head `497748421055b8ea256e62fbae296e9823c4206f` and was merged as
`69661f48485a8b1b0089e70862b2e2937f4157bc`. It reports the current step only;
it does not navigate later steps or submit.

The user explicitly selected automatic completion, including final submission,
for ONE suitable verified vacancy on 2026-11-04, with verified facts and clear
eligibility; stop on ambiguity. This changes the product acceptance goal from
Issue #79's original pre-submit-only MVP. It does not remove independent review,
exact-target/asset binding, CAPTCHA/new OAuth gates, confirmation evidence,
duplicate prevention, TEST-first verification or the prohibition on paid APIs.
No general PROD release or multi-job rollout is granted.

## Defects addressed in this patch

1. The prepare executor previously opened a page and performed fills/uploads
   without a request fence. DOM input events could autosave an application even
   without clicking Submit. The fence is now installed before page creation;
   service workers are blocked, WebSockets disconnected, popups stopped. Only
   the exact entry document and same-origin static resources without query
   strings load before preparation. Once candidate data may be present, ALL
   network traffic is denied, including same-origin GET exfiltration. A denied
   request stops preparation. This is deliberately NOT an approved ATS upload
   or submit endpoint policy.
2. Exceptions after a write/upload previously lost the accumulated counters.
   Counters now increment before each attempted mutation and survive failures.
   Raw browser exception text is not returned. An uncertain attempt requires
   reconciliation, never a blind retry.
3. Current target, auth, CAPTCHA and form structure are rechecked around each
   write/upload. Approved CV bytes are checked immediately before attachment
   and supplied from the verified memory buffer, not a mutable path.
4. The live manifest previously supplied missing review bindings and silently
   treated string booleans or absent asset approval as approval. Live input now
   requires exact target, reviewed form fingerprint, source SHA, strict booleans,
   reviewed timestamps and an explicit approved CV. Field values must equal
   separately pinned, verified, auto-fill-approved facts with source references.
   Missing/duplicate/contradictory facts and forbidden legal/consent fields stop.
   The current safe-writer allowlist is preserved; user authorization alone does
   not implement truthful handling of additional legal question types.
5. Live CLI now checks the actual checkout SHA and clean source, and skips the
   unfenced generic preliminary browser inspection. Inspection and preparation
   occur in the executor's fenced context. A current observation cannot replace
   a missing reviewed fingerprint. Empty file results cannot mean approved CV.

## Private reviewed inputs

The live CLI requires a private `EJS-LIVE-PREPARE-1` manifest, a separate
`EJS-VERIFIED-FACTS-1` file, and independent SHA-256 review pins for BOTH files.
Pins belong to the reviewer/environment, not to editable input JSON. JSON bytes
are hashed exactly, without adding a newline. This input check does not
cryptographically identify a reviewer or replace the independent approval gate.

The manifest binds `source_sha` (actual clean checkout), exact `target_url`,
`expected_form_fingerprint` (existing SR runtime format: 24 hex characters),
`opportunity_key`, `requisition_id`, `candidate_profile_version`, `observed_at`,
`reviewed_at`, `reviewer_ref`, field/upload plans and control resolutions.
Reviewed form observation is limited to 24 hours. Each fact binds canonical field,
exact value, source reference, literal `verified=true`, literal
`auto_fill_approved=true`, and `verified_at` within 30 days. These are conservative
loader limits, not proof that a vacancy or immigration status is valid.

CV resolution must identify the SAME approved CV artifact, canonical field and
required status as its upload plan. All planned controls must be covered exactly
once. The fixture-only schemas in `tests/test_live_canary_manifest.py` illustrate
these contracts without real candidate data.

The existing canary environment additionally needs private verified-facts JSON
and independently reviewed manifest/facts digests. No secrets are created by this
patch. Materialized inputs are kept outside the checkout in runner temporary storage, use owner-only creation permissions and are removed
in an always-run cleanup step. No actual CV, profile, address or email code belongs
in Git, issue text, output evidence or logs.

## Remaining acceptance gates

| Gate | Actual status |
| --- | --- |
| Read-only render/network/shadow/step diagnostics | Implemented; PR #87 merged; not later-step acceptance |
| Safe preparation network and error evidence | Implemented in this patch; independent review and exact-head CI required |
| Fact-backed manifest | Validator and CLI integration in this patch; real approved live manifest absent |
| Fresh suitable target | Not verified; cached tracker status is insufficient |
| Scoped Shadow DOM/frame and multi-step writer | Not implemented; generic executor still stops on unsupported structure |
| Retained interactive auth/challenge session | Not integrated; fresh executor context cannot reuse a user's solved challenge |
| Reviewed employer autosave/upload endpoints | Not implemented; conservative fence intentionally blocks them |
| Actual live profile/CV readback | Not proved by fixture results |
| Single-submit, durable intent/idempotency and employer confirmation | Not implemented by prepare executor; R2 helper only emits a disabled release manifest |
| Applied state reconciliation | Must consume confirmed employer outcome; never infer success from a click |

ADP PR #60 and AWLI/email-code PR #72 are still separate draft development;
they must not be described as released main capabilities. Do not merge the large
ADP branch wholesale as a shortcut to acceptance.

## Free implementation path

Use the existing local Mac Python/Playwright/Chromium environment; no new hosted
service, subscription or paid inference API is needed for deterministic browser
steps. Build one ATS adapter and one retained session before expanding to more
platforms. Human CAPTCHA remains an explicit handoff.

Playwright locators support waiting and exact input/file interactions; avoid
blind sleeps and arbitrary JavaScript click fallbacks:
https://playwright.dev/python/docs/locators
https://playwright.dev/python/docs/input
https://playwright.dev/python/docs/network

SmartRecruiters Application API requires protected customer/partner credentials
and the `candidate_applications_manage` OAuth scope. Do not assume an applicant
account grants API access or introduce a paid API workaround:
https://developers.smartrecruiters.com/docs/application-api
https://developers.smartrecruiters.com/docs/authentication

## Vacancy evidence from this audit

The cached Version 1 `business-analyst-ref6532m` URL redirects to the general
careers page, so it is not proof of a currently open requisition. The Rituals
Supply Chain Fulfilment & Logistics page explicitly says the job has expired;
its cached To Apply status must not authorize preparation. A different Version 1
Senior BA posting is visible, but vacancy-specific sponsorship and all mandatory
fit conditions remain unverified. No target was promoted to eligible and no
employer form was filled, CV uploaded or application submitted in this audit.

Sources (read 2026-11-04):
https://www.version1.com/careers/job-listing/business-analyst-ref6532m/
https://jobs.smartrecruiters.com/Rituals1/744000127368309-business-analyst-supply-chain-fulfilment-logistics
https://jobs.smartrecruiters.com/Version1/744000136002936-senior-business-analyst
