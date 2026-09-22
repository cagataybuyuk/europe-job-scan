# GD-004 ADP Verified Session Bootstrap

Status: design approved by live evidence; implementation next

## Live evidence

Run `35720538458` completed the reviewed profile-v2 flow successfully through:

- reviewed OneTrust opener;
- Unselect All;
- Save Changes;
- Apply;
- three identity writes;
- TR country selection;
- phone write with semantic readback;
- one reviewed Continue click.

The post-Continue surface is a required email verification step, not the application form itself.

Observed verification contract:

- required control id: `oneTimePassWord`;
- label: `Enter the Verification Code`;
- channel: email;
- visible `verify` button;
- Verify is disabled before a code is entered;
- notice confirms that a verification code was sent to the candidate email address;
- no application CV/upload/submit controls are yet authorized.

## Policy

The email verification code is treated as a **verification/auth boundary**, not as an ordinary application field.

The system must not:

- guess a code;
- scrape or bypass an MFA/CAPTCHA/security challenge;
- put a verification code in workflow-dispatch inputs, logs or artifacts;
- silently broaden the existing profile-v2 canary to enter credentials;
- mark an application as started/completed merely because the verification gate was reached.

The canonical human-review reason is `verification_required`.

## Production strategy

The preferred ADP production path is **verified-session reuse**:

1. establish a verified ADP guest session with explicit user participation;
2. capture only browser session state required for reuse;
3. store that state as a protected GitHub environment secret, never in source control or Sheets;
4. production runs first test whether the stored session still grants access to the application flow;
5. when valid, continue to the read-only application manifest and normal adapter gates;
6. when expired/revoked, fail closed to `human_review:verification_required`.

This aims to make verification an occasional session-bootstrap event instead of a repeated per-application manual step where ADP permits session reuse.

## Bootstrap authority

The future bootstrap tool may:

- open the exact reviewed ADP target in a local/headful browser;
- reproduce only the already reviewed cookie/Apply/profile/Continue path;
- stop at the exact verification surface;
- allow the user to type the received verification code directly into the browser;
- wait for the verified transition;
- export Playwright storage state;
- upload that state to the protected environment through byte-safe secret provisioning;
- delete local temporary session material.

The bootstrap tool must not read the user's email inbox, automate OTP extraction, bypass a security challenge, upload a CV, answer application questions or click final Submit.

## Secret handling

Proposed protected secret:

`EJS_ADP_VERIFIED_STORAGE_STATE_JSON`

Requirements:

- scoped to `gd004-safe-fill-upload-canary` initially;
- provisioned through stdin/file redirection, not command-line secret arguments;
- never printed;
- temporary files zero-overwritten best-effort and deleted;
- no raw cookie/token/session-state values in artifacts;
- diagnostics expose only non-secret state such as `session_loaded`, `verification_surface_present`, `application_surface_present`, fingerprints and control counts.

## Validation sequence

Before using the session for any new mutation authority:

1. local bootstrap succeeds with user-entered verification;
2. a GitHub-hosted **read-only verified-session inspector** loads the protected storage state;
3. the inspector proves whether the session survives on a fresh runner;
4. if valid, it captures the post-verification application control manifest;
5. only after that manifest is reviewed do CV upload or application-answer writes receive separate canary authority.

## Failure modes

- session state too large for GitHub secret -> move to encrypted artifact/store with separate key;
- server ties verification to a browser instance/device -> keep verification as explicit human-review boundary;
- session expires quickly -> set conservative TTL/health check and avoid false retries;
- verification surface changes -> fail closed and require fresh evidence;
- CAPTCHA/MFA/security-verification appears -> stop; no bypass.

## Next implementation

1. explicit verification-surface classifier (this milestone);
2. local/headful verified-session bootstrap;
3. byte-safe protected session secret provisioning;
4. read-only fresh-run session inspector;
5. post-verification application manifest canary.
