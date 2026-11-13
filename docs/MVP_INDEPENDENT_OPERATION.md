# First MVP: operation without Codex

User architecture decision, 2026-11-13: the first reusable MVP must run even when
Codex is closed. A flow that depends on Codex browser/Drive connectors is not the
accepted final operating model. This decision does not grant deployment, new
OAuth scopes, an additional employer submission or a general rollout.

## Acceptance boundary

One separately reviewed suitable target must run from the application itself:
load approved private facts/CV, start its own browser, fill/read back the reviewed
form, upload the approved CV, submit at most once under the exact operation
grant, verify the employer acknowledgement and publish/read back its result.
Close Codex during the acceptance run. Preserve the same canonical ledger and
the consumed Version 1 record through restart, interruption and reporting errors.
An uncertain employer result must never trigger another application POST.

CAPTCHA, new OAuth permissions, changed mandatory facts and unknown controls
remain human handoffs. The application needs a durable Waiting for user state
and a way to notify the user; it cannot claim unconditional zero-touch operation.
Known candidate facts are still filled by the application.

## Remaining implementation

1. Package a directly runnable entry point and durable process lifecycle with
   status, stop, restart and same-ledger recovery. Decide the local-Mac operating
   assumptions explicitly before enabling any background service; a sleeping or
   offline Mac cannot be advertised as continuously available.
2. Provide independent, narrowly scoped access to approved private facts/CV and
   the central destination. Existing Codex connector sessions are not runtime
   credentials. Any new authentication grant remains a separate human action.
3. Complete a real TEST destination integration and truthful result receipts.
   The current Apps Script prototype is synthetic only. Its cooperative lock
   does not make manual edits or connector writers globally atomic; neither a
   claimed capability nor a read-then-write sequence can substitute for that.
4. Connect the reviewed employer-native controls and an explicit same-application
   handoff/recovery policy. Current reviewed execution deliberately claims the
   preparation scope permanently before opening a browser and does not retry it.
5. Run independently reviewed TEST/restart/failure checks, then the separately
   approved exact live target with employer and central readback. CI alone is not
   live acceptance.

Use deterministic local execution where possible. No paid model API, purchase,
quota reset, deployment or free/unlimited cloud assumption is introduced. GD-005
remains advisory and is not connected to this runtime by this decision.

## Verified starting point

PR #103 implements the exact Devoteam validator/command and one additional
reviewed scope in the same database, disabled without its separately supplied
grant. It is not an independently operated service or a deployed central
publisher. Native SPL month/profile/City/phone compatibility is a separate
reviewed change; employer-side acceptance remains to be demonstrated.
