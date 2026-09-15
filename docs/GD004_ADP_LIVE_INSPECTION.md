# GD-004 ADP Workforce Now live inspection

This layer inspects an explicitly approved `https://workforcenow.adp.com/...` URL without application mutation authority.

## Allowed

- Navigate to the exact approved ADP URL.
- Read allowlisted structural metadata for visible controls and actions.
- Inspect same-origin frames and open Shadow DOM read-only.
- Record cross-origin frame origin metadata without traversing their DOM.
- Classify CAPTCHA, authentication, application-entry, inspectable-form, or unresolved states.

## Forbidden

- Clicking Apply / Apply Now.
- Entering credentials or candidate values.
- Uploading a CV or any other file.
- Solving or bypassing CAPTCHA/challenges.
- Clicking Next/Continue/Submit.
- Authorizing safe-fill or final submit from inspection evidence.

## Routes

`captcha_boundary` or `auth_boundary` -> `human_handoff`.

`application_entry_observed` -> `navigation_review_candidate`. This only permits review of a future one-click navigation canary; `navigation_click_allowed` remains false in the inspection route.

`inspected` with controls -> `manifest_review_candidate`. Safe-fill stays disabled until a scoped manifest is reviewed and explicitly promoted.

Everything else -> `diagnostic_review`.

All route outputs keep `automation_resume_allowed=false`, `navigation_click_allowed=false`, `safe_fill_allowed=false`, and `final_submit_allowed=false`.
