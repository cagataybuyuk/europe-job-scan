# Next MVP test target: Devoteam Business Analyst

Selected on 2026-11-12 for read-only form validation and TEST development:

- Employer: Devoteam Portugal / public identifier `Devoteam`.
- Role: Digital Impulse | Business Analyst; Lisboa, Portugal, hybrid; permanent.
- Posting: `744000156831399`; requisition `REF00063`.
- Publication: `3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e`.
- [Official posting](https://jobs.smartrecruiters.com/Devoteam/744000156831399-digital-impulse-business-analyst-).
- [Official Posting API](https://api.smartrecruiters.com/v1/companies/Devoteam/postings/744000156831399).
- API returned `active: true`; released 2026-10-21. This is a timestamped
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
picker with a Year control and twelve month cells. Current reviewed corrections
support only native `input[type=month]`; they must stop on this real picker until
a narrowly reviewed adapter, month-only semantics and readback tests are added.
Accessibility labels represent month cells as day 1; that does not establish a
candidate's actual day-level employment dates.

The first Version 1 scope stays Confirmed/consumed. This record grants no new
final submission, new ledger, OAuth permission or general rollout. Real upload,
CV-parser output, complete screening/submit behavior and backend company identity
remain to be reviewed before exact-target execution can be enabled.

Further read-only resource inventory observed the same rendered form loading
`/oneclick-ui/api/company/5b8f87c1e4b07eb5549de6e8/publication/3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e/config`.
That backend company ID also matches its public Devoteam Portugal logo path.
Direct unauthenticated GET returned 403; its response payload was not read and no
alternate authentication, challenge bypass or cookie import was attempted.
Actual upload/application endpoint purpose and current payload compatibility
still require review before execution, independent of these identity signals.
