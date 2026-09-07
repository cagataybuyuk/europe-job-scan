# BE-1 — Browser Worker Bootstrap

BE-1 implements the first controllable Chromium/Playwright runtime for Europe Job Scan.

## Authority

The worker is **read-only by construction**. `BrowserWorkerAuthority` rejects form-value writes, file uploads, credential entry, consent actions, CAPTCHA bypass and final Submit before Chromium launches.

## Runtime behavior

- Opens one canonical application URL with bounded navigation.
- Renders JavaScript through real Chromium.
- Extracts normalized input/textarea/select metadata, requiredness evidence and options.
- Observes buttons/actions (including Submit) without clicking them.
- Detects coarse auth/CAPTCHA boundaries.
- Produces deterministic page and form fingerprints.
- Persists no raw DOM, cookies, credentials or candidate values.
- Returns typed failures for blocked/unsupported navigation.

## Environment finding

The current execution container contains Playwright and `/usr/bin/chromium`, so browser control is now technically connected. Local HTTP/JS rendering and DOM extraction pass. External browser egress is currently blocked by the host runtime (`ERR_BLOCKED_BY_ADMINISTRATOR`), so the live Rituals SmartRecruiters URL returns a typed `access_blocked` result rather than being misclassified as a closed vacancy or empty form.

This means BE-1 runtime bootstrap is complete, while a live employer-form DOM smoke requires an execution environment with outbound browser access.

## Scope exclusions

BE-1 does not fill fields, upload files, click consent, handle credentials, bypass CAPTCHA, or submit an application. Those authorities remain reserved for later browser execution waves and SUBMIT-2.
