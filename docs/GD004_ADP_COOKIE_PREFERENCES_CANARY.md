# GD-004 ADP Cookie Preferences Inspector

## Why this exists

Live safe-fill run `34980372892` observed a visible OneTrust banner but no directly actionable `Deny` or `Agree` control. The only visible cookie actions were `To manage your preferences, click here` and `Close`. The safe-fill canary therefore stopped fail-closed with `ADP_COOKIE_DENY_CONTROL_NOT_AVAILABLE` before any cookie click, Apply click, candidate write, upload, or submit.

## Purpose

This canary performs exactly one reviewed UI action: it clicks the visible OneTrust control named `To manage your preferences, click here`. It then inspects the resulting preference-center surface read-only and records non-secret evidence about visible buttons and checkboxes.

The output includes a stable SHA-256 fingerprint over the visible preference-center descriptor so a later cookie-policy canary can be constrained to an evidence-backed exact surface rather than guessing or using force-click/retry behavior.

## Authority boundary

Allowed:

- navigate to the exact approved ADP URL;
- verify the approved ADP pre-navigation surface fingerprint;
- exactly one normal click on the unique visible/enabled `To manage your preferences, click here` button;
- read-only inspection of visible preference-center button IDs/labels/enabled state;
- read-only inspection of visible preference-center checkbox IDs/labels/checked/enabled state.

Forbidden:

- changing any cookie toggle;
- saving cookie preferences;
- accepting cookies;
- rejecting cookies;
- clicking `Close`;
- clicking application `Apply`;
- writing candidate values;
- credentials;
- file upload;
- CAPTCHA/challenge bypass;
- final Submit.

## Live execution gate

Workflow: `GD-004 ADP Cookie Preferences Inspector`

Approval phrase: `APPROVE-TEST-ADP-OPEN-COOKIE-PREFERENCES`

The workflow uses the existing `gd004-safe-fill-upload-canary` environment so the R2 deployment reviewer remains required.


## Opener contract revision — 2026-09-22

Live run `35716218979` showed that OneTrust can render two preference-related controls at the same time:

- legacy `#ot-sdk-btn`, retaining the older “To manage your preferences…” label;
- banner CTA `#onetrust-pc-btn-handler`, currently exposed as “Set your preferences…” and previously observed with the older label variant.

The legacy text-based locator could report visible/enabled and still time out during the normal Playwright click. The reviewed opener is therefore now bound to the exact stable banner CTA ID `#onetrust-pc-btn-handler`.

The contract accepts only the two live-observed accessible-label prefixes for that exact ID. It does not use a generic text match, force-click, DOM-dispatch click, retry loop, optional-cookie acceptance, or broader cookie authority.
