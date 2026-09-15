# GD-004 ADP Continue Canary

This canary advances the reviewed ADP guest-identity flow by exactly one application action after the already verified identity safe-fill.

## Evidence base

Live run `34986082662` verified the bounded sequence:

- open OneTrust preference center;
- `Unselect All`;
- `Save Changes`;
- one `Apply` click;
- first name, last name, and email exact write/readback;
- no upload and no submit.

The reviewed post-fill visible-button surface is:

- `Continue`;
- `Sign in with LinkedIn`;
- `Sign in with Google`;
- `Sign in with Facebook`;
- `To manage your preferences, click here`;
- `Close Menu`.

Stable post-fill action-surface SHA-256:

`0e3e60f904525ad95f5244ce99a29a335928b01992f066cc4da9d45753a7bd53`

## Runtime authority

The canary may perform at most:

1. one preference-center navigation click;
2. one `Unselect All` click;
3. one `Save Changes` click;
4. one reviewed `Apply` click;
5. up to three AUTO_SAFE identity `fill()` writes;
6. one exact `Continue` click;
7. read-only inspection of the resulting page.

It must not interact with phone/country controls, social sign-in, credentials, file upload, CAPTCHA/challenge controls, another application navigation action, or final Submit.

## Fail-closed routing after Continue

- auth/CAPTCHA boundary -> `human_handoff`;
- visible application controls -> `manifest_review_candidate`;
- otherwise -> `diagnostic_review`.

All routes keep automation resume, further safe-fill, file upload, and final submit disabled pending review.

## Workflow

`.github/workflows/gd004-adp-continue-canary.yml`

The workflow is manual, immutable-SHA pinned, R2 environment-gated, and requires the exact phrase:

`APPROVE-TEST-ADP-CONTINUE-AFTER-IDENTITY-SAFE-FILL`
