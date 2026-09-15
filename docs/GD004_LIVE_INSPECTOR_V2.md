# GD-004 Live Inspector v2

Status: implementation active; no live mutation authority added.

## Purpose

The first live inspection proved GitHub-hosted Chromium can reach the approved SmartRecruiters OneClick URL while preserving zero field writes, zero uploads and zero submits. That run returned no native controls or actions, so a safe-fill manifest could not be trusted.

Live Inspector v2 expands read-only structural evidence without changing the authority boundary.

## Read-only diagnostics

The inspector records only allowlisted structural metadata:

- document readiness and render-wait exit reason,
- element/form/native-control/action-candidate counts,
- script and iframe counts,
- open Shadow DOM host count,
- custom-element tag inventory (capped),
- custom-control hints based on structural ARIA attributes,
- same-origin iframe structure inspection,
- cross-origin iframe origin only, without inspecting its DOM,
- known CAPTCHA-delivery frame evidence when the origin matches `captcha-delivery.com`.

It does not retain HTML, form values, cookies, storage, candidate data or file contents.

## Render readiness

After `domcontentloaded`, the inspector performs a best-effort `networkidle` observation and then polls only structural counters. It exits when a structure signal appears, when the document is stable without an inspectable form, or when the bounded render-wait budget is exhausted.

This replaces dependence on a single fixed two-second sleep while keeping the inspection bounded.

## Fail-closed discovery state

A successful browser/navigation run with no inspectable native controls and no known challenge evidence is reported as:

- `runtime_state = form_structure_not_discovered`
- `error_code = FORM_STRUCTURE_NOT_DISCOVERED`
- `live_execution_ready = false`

This state is diagnostic success, not execution readiness. No safe-fill manifest may be produced from it.

If native controls are observed, the run may report `runtime_state = inspected`, but `live_execution_ready` remains false until a reviewed manifest and later canary gate explicitly authorize execution.

## CAPTCHA frame boundary

The 2026-09-15 Version1 live run observed a cross-origin `https://geo.captcha-delivery.com` iframe while the main SmartRecruiters document contained zero controls and zero actions. The inspector must classify this as:

- `runtime_state = captcha_boundary`
- `error_code = CAPTCHA_BOUNDARY`
- `captcha_observed = true`
- `live_execution_ready = false`

The classification uses only the cross-origin frame origin. The challenge iframe DOM is never inspected and no CAPTCHA bypass is attempted.

## Fail-closed routing

Each live inspection now produces a second artifact, `live-route.json`, derived only from the read-only inspection report. The router never opens a browser and cannot authorize execution.

Routing outcomes are:

- `human_handoff`: challenge evidence such as `CAPTCHA_BOUNDARY` is present. The user must handle the site-required challenge in a trusted interactive browser. Automation must not resume until a fresh read-only inspection no longer reports the boundary.
- `manifest_review_candidate`: inspectable controls are present without a challenge boundary. This permits only human review and scoped manifest preparation; safe-fill is still disabled.
- `diagnostic_review`: no trustworthy control structure is available. Continue read-only diagnostics before any live write path is considered.

Every route keeps:

- `automation_resume_allowed = false`
- `safe_fill_allowed = false`
- `final_submit_allowed = false`

This routing layer complements the existing GD-004 durable queue behavior where non-rendered browser outcomes are reconciled as `REVIEW_REQUIRED`; it does not replace or weaken that gate.

## Frame policy

Only same-origin child frames are inspected. Other cross-origin frames are reported as `cross_origin_not_inspected`; their DOM is not traversed. Known CAPTCHA-delivery origins may be classified as challenge evidence using the origin string alone. Frame observations are rebased into unique scope identities before aggregation so controls from separate documents cannot collide.

## Authority boundary

Live Inspector v2 still contains no operation for:

- field fill/write,
- select changes,
- clicking Apply/Next/Continue,
- file upload,
- final Submit,
- CAPTCHA bypass,
- authentication or credential entry.

The manual workflow continues to require an immutable source SHA and explicit `APPROVE-TEST-LIVE-INSPECTION` phrase, with `EJS_FINAL_SUBMIT=false` and `EJS_KILL_SWITCH=true`.

## Promotion criterion

Review each uploaded `live-inspection.json` and `live-route.json` artifact before any manifest is prepared.

- If native controls are discovered and the route is `manifest_review_candidate`, prepare a reviewed scoped manifest from the observed identities and proceed toward the safe-fill + single-CV-upload canary only after explicit promotion.
- If the route is `human_handoff`, do not attempt CAPTCHA bypass. Use a trusted interactive browser for the site-required challenge or choose another approved canary target that does not present a challenge to the execution environment.
- If only custom elements, closed Shadow DOM indicators, landing-page actions or other cross-origin frames are observed, build the smallest read-only adapter needed for that structure before any live writes.
- If the route is `diagnostic_review`, investigate render/network evidence before changing execution authority.
