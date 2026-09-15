# GD-004 Live Inspector v2

Status: implementation candidate; no live mutation authority added.

## Purpose

The first live inspection proved GitHub-hosted Chromium can reach the approved SmartRecruiters OneClick URL while preserving zero field writes, zero uploads and zero submits. That run returned no native controls or actions, so a safe-fill manifest cannot yet be trusted.

Live Inspector v2 expands read-only structural evidence without changing the authority boundary.

## Read-only diagnostics

The inspector now records only allowlisted structural metadata:

- document readiness and render-wait exit reason,
- element/form/native-control/action-candidate counts,
- script and iframe counts,
- open Shadow DOM host count,
- custom-element tag inventory (capped),
- custom-control hints based on structural ARIA attributes,
- same-origin iframe structure inspection,
- cross-origin iframe presence without inspecting its DOM.

It does not retain HTML, form values, cookies, storage, candidate data or file contents.

## Render readiness

After `domcontentloaded`, the inspector performs a best-effort `networkidle` observation and then polls only structural counters. It exits when a structure signal appears, when the document is stable without an inspectable form, or when the bounded render-wait budget is exhausted.

This replaces dependence on a single fixed two-second sleep while keeping the inspection bounded.

## Fail-closed discovery state

A successful browser/navigation run with no inspectable native controls is reported as:

- `runtime_state = form_structure_not_discovered`
- `error_code = FORM_STRUCTURE_NOT_DISCOVERED`
- `live_execution_ready = false`

This state is diagnostic success, not execution readiness. No safe-fill manifest may be produced from it.

If native controls are observed, the run may report `runtime_state = inspected`, but `live_execution_ready` remains false until a reviewed manifest and later canary gate explicitly authorize execution.

## Frame policy

Only same-origin child frames are inspected. Cross-origin frames are counted and reported as `cross_origin_not_inspected`; their DOM is not traversed. Frame observations are rebased into unique scope identities before aggregation so controls from separate documents cannot collide.

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

After this change is merged, rerun the same approved Version1 SmartRecruiters URL. Review the uploaded `live-inspection.json` artifact.

- If native controls are discovered, prepare a reviewed scoped manifest from the observed identities and proceed toward the safe-fill + single-CV-upload canary.
- If only custom elements, closed Shadow DOM indicators, landing-page actions or cross-origin frames are observed, build the smallest read-only adapter needed for that structure before any live writes.
- If the page remains structurally empty, investigate render/network/bot boundary evidence before changing execution authority.
