# GD-004 ADP Verified Session Bootstrap

Status: bootstrap and inspector implemented; live session reuse not yet proven

## 2026-09-22 early-export correction

Run `35726250341` loaded 11 cookies and one origin, clicked the reviewed Apply
entry, and stopped with `ADP_VERIFIED_SESSION_NOT_RECOGNIZED_IDENTITY_SURFACE`.
The preceding local bootstrap reported zero visible controls. The user observed
the window closing immediately after Verify, without seeing the application form.
This establishes a failed reuse attempt; it does not establish whether ADP forbids
session transfer. The old bootstrap incorrectly treated two absent-OTP polls as
success, including blank navigation/loading screens.

Bootstrap v2 requires observed OTP, absent OTP/guest identity, a minimum ten-second
settling period after the last OTP observation, and an unchanged, non-empty form
control structure for at least five seconds. Empty surfaces, buttons, search and
known OneTrust controls, disabled/read-only fields and password screens cannot
satisfy that signal. DOM observation errors reset stability. Unknown destinations
and new windows stop the bootstrap. Unsupported frames/custom-only controls may
time out conservatively; no selectors are invented for an unseen application form.

This is a **candidate surface**, not a reviewed application manifest. It does not
authorize application writes, upload or Submit. The helper now runs the existing
read-only inspector in a fresh local browser before updating the protected secret.
It uses the same reviewed URL, navigation fingerprint and Apply ordinal and makes
at most one Apply click. A failed bootstrap or replay leaves the existing secret
unchanged, and temporary state/reports are cleaned in either case. The inspector
also stops at a post-Apply authentication/CAPTCHA boundary.

After this PR is reviewed, test its branch locally with the existing command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap_adp_verified_session.ps1"
```

Enter the verification code only in the browser. Keep the window open after
Verify; no CV upload or final Submit is required. Expected outcomes:

- `FORM_NOT_READY`: no stable candidate form was observed; no secret update.
- Local inspector blocked: candidate state did not survive a fresh local browser;
  no secret update. Investigate state capture/ADP session semantics before retrying
  the GitHub runner.
- Local replay and secret update succeed: test GitHub-hosted reuse next, using the
  exact reviewed main SHA after merge. Local success does not prove cross-runner reuse.

The fingerprint default is specific to the reviewed job. A different target needs
its own reviewed fingerprint and ordinal; do not relax the fingerprint gate.

### Follow-up: cookie visibility during fresh local replay

The corrected local bootstrap subsequently observed 21 visible controls and
exported candidate state. Its fresh local replay stopped with
`ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED`, before any Apply click. The existing
secret was not changed. This result does not establish loss of the verified guest
identity: the cookie gate prevented the authentication reuse test from running.

Inspector v1 checked OneTrust visibility immediately at `domcontentloaded`.
That timing could mistake a transient banner for persistent consent loss; the
available log cannot distinguish the two. Inspector v2 observes the full bounded
render window (10 seconds by default), requires both reviewed OneTrust containers
to remain absent for at least the final second, and checks them again after the
render snapshot before Apply. Persistent, delayed or unreadable cookie UI still
blocks, with zero cookie clicks. No cookies are rewritten and no preference is
accepted or rejected automatically.

The console now includes only storage counts and `cookie_gate` evidence: initial,
final and pre-Apply visibility, the observation window, clear duration, observation
error count and zero cookie clicks. No cookie values, labels or storage content
are exposed. A banner disappearing during this window supports a timing issue;
a persistent banner still needs investigation of consent persistence before
drawing conclusions about identity reuse.

For the next manual bootstrap, if OneTrust is shown, use the previously reviewed
Preferences -> Unselect All -> Save Changes path before Apply. Merely closing a
banner is not evidence that the consent choice was persisted. Enter OTP only in
the ADP browser and do not upload or submit anything.

### Follow-up: identity returns after cookie gate clears

The next corrected local run observed a stable post-verification form with 21 visible
controls and exported 11 cookies plus one origin. Fresh local replay then completed
the passive OneTrust gate, clicked the single reviewed Apply entry, and returned to
the guest identity surface with
`ADP_VERIFIED_SESSION_NOT_RECOGNIZED_IDENTITY_SURFACE`. This is stronger evidence
than the earlier cookie-gate failure: ordinary Playwright storage state alone did
not preserve the verified guest identity in a fresh browser context for this run.

Playwright documents that normal storage-state reuse does not persist
`sessionStorage`. Before changing the protected secret format or GitHub workflow,
bootstrap v3 captures the current origin's sessionStorage only to a sensitive
temporary local file, reports counts/byte size only, and deletes the file after the
run. Inspector v3 can restore that state into a fresh local context. The PowerShell
helper first tests ordinary storage-state reuse; only on the exact guest-identity
failure does it perform a second read-only replay with sessionStorage restored.

A successful second replay is diagnostic only: it deliberately leaves the existing
GitHub secret unchanged and stops with a message that protected sessionStorage
transport must be implemented next. A failed second replay means another
browser-/server-scoped state mechanism must be investigated. Neither path adds
field writes, credential automation, upload or Submit authority.

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
2. a fresh local **read-only verified-session inspector** must pass before secret provisioning;
3. the GitHub-hosted inspector proves whether the session also survives on a fresh runner;
4. if valid, it captures the post-verification application control manifest;
5. only after that manifest is reviewed do CV upload or application-answer writes receive separate canary authority.

## Failure modes

- session state too large for GitHub secret -> move to encrypted artifact/store with separate key;
- server ties verification to a browser instance/device -> keep verification as explicit human-review boundary;
- session expires quickly -> set conservative TTL/health check and avoid false retries;
- verification surface changes -> fail closed and require fresh evidence;
- CAPTCHA/MFA/security-verification appears -> stop; no bypass.

## Next live validation

1. rerun the corrected local/headful bootstrap with user-entered verification;
2. inspect the fresh local replay result;
3. if successful, rerun the GitHub-hosted inspector;
4. review the real post-verification application manifest before authorizing new actions.
