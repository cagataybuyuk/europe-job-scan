# First single-application durable intent — local TEST engineering

## Behavior and integration

The current submit repository is in-memory. A process restart loses its duplicate
record, and the existing identity includes the profile version. The new private
SQLite ledger retains one immutable intent slot: a different target, execution,
profile or submit key cannot obtain another slot after reservation. There is no
reset/delete/retry API. SQLite is in Python's standard library; no new service,
paid API or package dependency is introduced.

`DurableSingleSubmission` connects an existing typed preparation receipt and
SUBMIT-1 validator to that ledger. It accepts no browser, page, callback, arbitrary
command or transport, and makes no network call. Default `SubmitAuthority()` stays
off; no intent is consumed while runtime submission is disabled. No environment
flag, release workflow or employer endpoint is enabled by this change.

The coordinator requires current MATCH evidence, a fresh observation (120 seconds),
R2 single-canary scope, explicit boolean authority, exact identity/URL/form binding,
complete required-control readback, strict successful optional readbacks and one
verified CV attachment. All returned control/canonical pairs must match the
reviewed request. A full intent proof binds CV content/version/filename hash,
answer hashes, source references and target as well as the semantic form. A later
CV/answer change under the same form/profile version cannot replace the intent.

The supported first-live target shape is an HTTPS SmartRecruiters posting, without
credentials, nondefault ports, query or fragment. File/loopback fixtures cannot
consume this slot. This shape check is NOT vacancy freshness, suitability,
sponsorship or employer-source authentication.

## State and failure rules

1. `reserve(...)`: revalidate receipt and existing policy, atomically record and
   commit the sole VALIDATED intent before returning `INTENT_RECORDED`.
2. `record_before_dispatch(...)`: independently recheck fresh receipt, authority
   and immutable proof binding, then durably record the uncertainty BEFORE the
   trusted driver may attempt an external effect. Output is
   `DISPATCH_INTENT_RECORDED`, not a claim that a click/network request occurred.
   The internal legacy status is `Submitted / Unconfirmed`.
3. A crash, timeout, ambiguous result or changed profile never frees the slot.
   If a process dies after marking but before sending, this conservative design
   can suppress an application that was never sent. Resolve that uncertainty by
   independent recovery evidence; do not silently create another database.
4. `reconcile(...)`: accept only matching submit/execution/requisition metadata,
   a literal true identity match, known confirmation enum, opaque evidence hash
   reference and an aware UTC observation between start and current time.
   Confirmation before a dispatch intent, stale/future/mismatched evidence and
   conflicting repeat confirmations are rejected.

**Source trust is external.** Matching confirmation metadata or an evidence hash
does not authenticate an employer. The caller must use a separately trusted
browser/email observer. The existing `applied_transition_allowed` helper is not
an employer-source verifier either. This service always reports
`source_authentication_performed=false` and `applications_write_performed=false`;
it does not reconcile a Sheet row to Applied. No live application completion is
proved by these records or fixture tests.

## Canonical path and privacy

Trusted bootstrap must pin ONE canonical private path, outside Git and shared by
all attempts for the first application. Never derive it from the job, profile,
execution, worktree, PR or caller-supplied ATS/AI manifest. A fresh database on a
second host or a copied/deleted ledger bypasses any per-file budget. This is a
single-host Mac/Linux primitive, not a distributed cross-provider ledger.
GitHub-hosted ephemeral jobs cannot use a new local SQLite file as live duplicate
protection; a separately reviewed shared control-plane adapter is required there.

The parent directory must be owner-only (0700) and database 0600. Symlink/hardlink,
unsafe permissions, unknown schema, corruption and pre-existing empty databases
are rejected. Identity references must be opaque bounded IDs: email addresses,
URLs and paths are rejected before hashing. Only SHA-256 identity/proof digests,
status, UTC times and an opaque evidence hash are stored. Answers, candidate data,
CV bytes/names, cookies, messages and email codes are not stored. Notes are ignored.

Transactions use `BEGIN IMMEDIATE`, synchronous FULL writes and commit before a
successful reservation/start returns. No reset/recovery code repairs a damaged
file into an empty budget. Storage errors fail closed with fixed messages.

## Validation and acceptance limits

160 targeted tests passed locally with zero skips, including real SQLite
multi-process competition, restart and crash cases, receipt/authority negatives,
existing SUBMIT-1 policy tests and one actual Chromium integration. The browser
test fulfills a synthetic HTTPS document locally and denies every other request;
no employer request is sent and no submit is clicked. Test databases are separate
private temporary files, never the canonical first-live ledger.

This primitive's original remaining work is implemented in a separate local lane
by PR #98; see [integrated execution and current acceptance limits](MVP_INTEGRATED_SINGLE_APPLICATION.md).
That lane connects pinned facts/CV bytes, multi-step observation, exact endpoints,
one dispatch and employer confirmation. The first real application was separately
confirmed through the reviewed interactive CUA canary on 7 November 2026. This is
not proof of unattended execution by the local command. Retained human-auth
handling, verified profile correction and central reconciliation retain their own
acceptance gates. All live TEST/PROD gates and the consumed canonical slot remain.
