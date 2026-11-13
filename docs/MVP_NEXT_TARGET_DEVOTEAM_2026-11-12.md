# Next MVP test target: Devoteam Business Analyst

Selected on 2026-11-12 for read-only form validation and TEST development:

- Employer: Devoteam Portugal / public identifier `Devoteam`.
- Role: Digital Impulse | Business Analyst; Lisboa, Portugal, hybrid; permanent.
- Posting: `744000156831399`; requisition `REF00063`.
- Publication: `3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e`.
- [Official posting](https://jobs.smartrecruiters.com/Devoteam/744000156831399-digital-impulse-business-analyst-).
- [Official Posting API](https://api.smartrecruiters.com/v1/companies/Devoteam/postings/744000156831399).
- API returned `active: true`, rechecked on 2026-11-13; released 2026-10-21. This is a timestamped
  observation and must be refreshed before any later employer action.

The engineering degree, ERP functional/business-analysis work, SQL, Agile/Scrum
and professional English requirements have corresponding candidate sources.
There is no stated fixed 3-5-year ERP minimum, mandatory local language, existing
work-right condition or explicit no-sponsorship exclusion in the reviewed text.
Employer sponsorship remains UNKNOWN. Candidate work authorization stays No,
sponsorship required Yes; support must never be inferred from missing text.

Read-only CUA followed the exact visible "I'm interested" action to the same
publication. The native form opened without an observed sign-in/CAPTCHA. It
showed contact fields, experience/education, profiles, CV, optional hiring-team
message, privacy acknowledgement and Submit. AWLI and Indeed are optional and
were not clicked. No personal data, file upload, Save or Submit was performed.

The empty experience editor was opened and canceled. A concrete compatibility
gap was observed: its From/To inputs are `type=text`, backed by a SPL month-grid
picker with a Year control and twelve month cells. A bounded SPL adapter and
separate City/text autocomplete support now have actual Chromium fixture tests
and independent review. See `MVP_NATIVE_PROFILE_CORRECTION.md` for the contracts.
Actual employer Save and backend City acceptance remain unproven.
Accessibility labels represent month cells as day 1; that does not establish a
candidate's actual day-level employment dates.

The first-page phone country button also has an empty native value despite its
visible selected country/dial prefix. The observed SPL select owns country ISO
and option metadata; its button receives the prefix through an assigned slot.
It requires the explicit phone-country adapter, not a generic shadow-host value
fallback. Candidate phone country and dial code must be separately source-backed.

The first Version 1 scope stays Confirmed/consumed. This record grants no new
final submission, new ledger, OAuth permission or general rollout. Real upload,
complete screening/submit behavior and real backend confirmation remain to be
verified before claiming live acceptance.

Further read-only resource inventory observed the same rendered form loading
`/oneclick-ui/api/company/5b8f87c1e4b07eb5549de6e8/publication/3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e/config`.
That backend company ID also matches its public Devoteam Portugal logo path.
Direct unauthenticated GET returned 403; its response payload was not read and no
alternate authentication, challenge bypass or cookie import was attempted.

## Public client review

The current page's actual loaded public client assets were inspected read-only.
The lower `oc-resume-upload input[type=file]` uses the attachment-only path:
validate file, upload resume, add attachment and set its form control. It does
not call the separate top Easy Apply resume parser. Prefer that reviewed lower
Resume input; unexpected profile cards still require source-bound review.

The upload service constructs
`/oneclick-ui/api/company/{company.id}/attachment/resume`, multipart field `file`
with a URL-encoded `filename` query parameter. The reviewed client consumes
`fileId`/`fileName`. Application routing uses metadata `jobId` only when supplied
by the actual route or `jid` query; it must not be inferred from posting API data.
This publication-only entry retains the company/publication application route.

These observations establish client purpose and identifier binding, not a real
upload receipt or backend response schema. In particular, the client can render
a success view for several response shapes; the runtime must not weaken its
confirmation test to a bare visible success view or an unverified hash. Unknown
submit responses remain Unconfirmed and cannot be retried automatically.

The existing first application's ledger is unchanged. A separate reviewed grant,
current facts/CV/endpoint pins and concrete exact-target user decision are still
required; none is issued by this document or by the TEST implementation.

The actual form links its privacy notice at
`https://www.devoteam.com/privacy-policy/`, which redirected to
`https://www.devoteam.com/personal-data/` on 2026-11-13. The notice was inspected
without accepting the unchecked application acknowledgement. The new employer's
data/CV transmission and legal acceptance remain separate action-time gates.
