# GD-004 ADP One-click Navigation Canary

## Purpose

Advance an approved ADP Workforce Now job-detail page through exactly one visible `Apply` entry action, then inspect the resulting page without any further mutation.

## Required evidence

The manual workflow requires all of the following from a fresh read-only inspection:

- exact immutable `main` SHA;
- exact approved ADP URL;
- exact 64-character schema fingerprint;
- exact document-scoped visible `Apply` observation key;
- explicit phrase `APPROVE-TEST-ADP-ONE-CLICK-NAVIGATION`.

The current live candidate that motivated this canary exposed two visible `Apply` actions. The canary never chooses between duplicates on its own; the workflow input must name exactly one observation key.

## Fail-closed preflight

Before the click, the canary re-renders the page and requires:

- `runtime_state=application_entry_observed`;
- no CAPTCHA or auth boundary;
- zero visible application controls;
- exact schema fingerprint match;
- exactly one approved entry action matching the supplied observation key and label;
- the resolved element is visible and enabled.

Any drift stops execution before the click.

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
