# GD-004 ADP One-click Navigation Canary

## Purpose

Advance an approved ADP Workforce Now job-detail page through exactly one visible `Apply` entry action, then inspect the resulting page without any further mutation.

## Required evidence

The manual workflow requires all of the following:

- exact immutable `main` SHA;
- exact approved ADP URL;
- exact 64-character navigation-surface fingerprint derived from reviewed read-only evidence;
- zero-based approved visible `Apply` ordinal within that reviewed surface;
- explicit phrase `APPROVE-TEST-ADP-ONE-CLICK-NAVIGATION`.

The navigation-surface fingerprint intentionally excludes hidden cookie-preference controls and raw DOM observation indices. It covers the visible application-entry surface and fail-closed boundary state, so harmless OneTrust/DOM index drift cannot grant or revoke click authority by itself.

The current live candidate exposed two visible `Apply` actions. The reviewed surface therefore contains two document-scoped `Apply` entries, and ordinal `0` selects the first one. The live DOM observation key is resolved only after the approved surface fingerprint matches.

## Fail-closed preflight

Before the click, the canary re-renders the page and requires:

- `runtime_state=application_entry_observed`;
- no CAPTCHA or auth boundary;
- zero visible application controls;
- exact navigation-surface fingerprint match;
- the approved `Apply` ordinal exists within the matched surface;
- the live resolved element is still a document-scoped supported observation identity;
- the resolved element is visible and enabled;
- the live label remains `Apply`.

Any drift stops execution before the click. A blocked preflight writes diagnostic evidence and keeps all application-mutation authority disabled.

## Authority

The canary has authority for one navigation click only. It does not force the click and does not retry it.

It never:

- enters credentials;
- fills application values;
- uploads a CV or other file;
- solves CAPTCHA/challenges;
- clicks a second action;
- submits an application.

After the click it produces a read-only structural snapshot and routes only to `human_handoff`, `manifest_review_candidate`, or `diagnostic_review`. Safe-fill and final submit remain disabled in all outcomes.
