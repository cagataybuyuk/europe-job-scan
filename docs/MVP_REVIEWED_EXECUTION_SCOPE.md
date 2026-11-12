# Reviewed second single-application scope — implementation only

The first Version 1 application stays Confirmed/consumed. Selecting a different
vacancy for tests does not grant a second final submission. No actual Devoteam
operation grant, canonical database migration, live preparation or application
is performed by this implementation. Devoteam's backend company-ID binding and
the exact-target user operation decision remain independent prerequisites.

## Independently reviewed operation grant

`review_execution_scope` is separate from the metadata-only TEST validator. It
requires an independently supplied `expected_decision_ref` (default `None` always
rejects), a separately reviewed complete `OneClickTarget`, exact raw-byte grant,
manifest and fact hashes, source commit, approved CV hash, profile version,
execution identity, reviewer and the unchanged Confirmed legacy snapshot digest.
The ordinary input compiler, private-file rules, actual CV-byte check, fresh
vacancy suitability and endpoint/form review still apply.

The only implemented future target is Devoteam REF00063 / posting
744000156831399 / publication 3d4a32a2-dc6b-4fd6-a0c2-f8e74336404e. Its full
backend company ID must match the independently reviewed caller target. The
literal `USER-DEVOTEAM-REF00063-LIVE-20261112` is an expected future decision
reference, **not evidence that such approval exists**. The caller may supply it
only from authenticated concrete user approval, never from grant/manifest bytes,
model output, this document or a vacancy. No actual grant file is supplied here.

The schema `EJS-REVIEWED-EXECUTION-SCOPE-1` binds literal operation coverage for
form write, CV upload, Next, final submit and canonical scope migration. Extra or
missing operations, numeric truthy substitutes and extra paths/flags reject.
Booleans alone cannot grant anything: all independent identity/source/byte pins,
the external decision and a current UTC review (at most 24 hours) must match.
Returned objects are sealed in process memory; constructed/replaced dataclasses
and objects from the metadata validator are not accepted by durable operations.
This validator authenticates neither the user nor the reviewer itself.

All six primitive target fields are sealed by value. The issued target is a clone
of reviewed bytes, not an alias of the caller's target. A nested target mutation
invalidates the grant even when a frozen dataclass is forcibly changed.

## Same existing database, additive fixed slot

`register_reviewed_execution_scope(legacy, scope, now=...)` is an explicit atomic
v1→v2 migration in the SAME bootstrap-pinned first-application SQLite database.
It leaves `first_application`, every column and its Confirmed row values intact.
It adds only two strictly validated tables: `reviewed_scope_grants` and
`reviewed_scope_attempts`, each restricted to the one fixed slot 2. The default
legacy view still returns Confirmed/occupied and cannot reserve another intent.
There is no third slot, replacement file, reset, release or arbitrary ledger-path
argument. Schema and grant insertion roll back together on failure.

The exact grant registration is idempotent; a replay cannot obtain another
preparation claim. A changed source, profile, CV, execution or grant cannot
replace the registered second slot. The original first-target identity and
snapshot remain bound. The fixed next target cannot alias the prior posting,
publication or company/requisition. No general multi-job history budget exists.

`claim_reviewed_preparation(...)` commits a permanent Preparing row BEFORE
browser preparation or upload. Across processes only one transaction succeeds.
The winner receives a non-exported random in-memory owner; only that view reports
no submit intent while Preparing. Other/restarted processes receive no view.
A crash, stopped preparation or uncertainty never releases/reconstructs this
claim. Review all form/endpoint facts before consuming it; there is no retry API.
The exact view identity is sealed to its legacy object/path, scope, owner bytes,
process and browser thread. Copies, reconstructed attributes, fork inheritance,
thread changes and path retargeting reject before operations. Capabilities and
private references are omitted from its repr. The trusted caller must invoke
only one owned runtime/browser per winning view; no general retry dispatcher is
provided by these ledger methods.

The owned view implements the existing runtime's ledger methods. Exact reviewed
execution/profile/requisition and the complete validated attempt bind reservation.
Preparing → Validated → Submitted / Unconfirmed commits before dispatch;
confirmation requires the matching trusted employer evidence. A changed profile
or submit key does not reopen the target. Expiry stops new preparation/reservation
and dispatch; later authentic confirmation may still be recorded without a retry.
The confirmation source remains the separately trusted employer observer.

## TEST evidence and remaining integration

Temporary private SQLite tests verify unchanged legacy data/default behavior,
atomic migration rollback, competing spawned processes, real process crash,
replayed or changed grants, scope identity binding, unknown schema/corrupt states,
expiry and ambiguous/late confirmations. No test opens the canonical user database
or an employer URL. Independent review, exact-source CI, actual backend binding
and concrete operation approval remain separate gates. Passing these tests is
not a live application receipt.

## Exact-target command

`python -m ejs.apps.reviewed_application` validates independently pinned standard
private inputs, source cleanliness, the approved CV bytes, native entry and the
exact Devoteam target by default. It opens no database/browser in that mode.
The command fixes its state root from the OS account database, keeps the SAME
`first-application.sqlite3`, and fixes its own local result filename to
`devoteam-ref00063-outcome.json`. No ledger/reset/target/output-path argument exists.
It requires existing nonempty canonical state and uses strict mode=rw validation,
without invoking the constructor's new-file initialization path.

Execution additionally requires `--execute-reviewed-application`, `--grant`,
`--grant-sha256` and the independently supplied exact `--user-decision-ref`.
The raw grant/manifest/fact reads check actual private descriptors and external
hashes; symlinks, hardlinks, non-private/FIFO files and changed bytes reject.
The command validates the operation grant against the actual legacy snapshot,
explicitly registers the additive migration, and permanently claims preparation
BEFORE creating its browser. A consumed claim returns without browser activity.

There is exactly one runtime invocation and no OAuth/cookie/handoff/retry bridge.
Blocked preparation remains consumed. Employer confirmation can write only the
fixed private local outcome, with `central_record_pending=true`; this command
does not automatically publish a live Sheet write. Cleanup, cancellation and
local record failure preserve a durable confirmed result rather than request a
second submission. Errors and stdout omit private values, paths and capabilities.

Bootstrap tests use the real private compiler, actual CV bytes, grant validation,
temporary canonical-shaped SQLite migration/claim and local outcome writer.
Only the browser/runtime in those bootstrap tests are synthetic. Real employer
compatibility and a command-driven live result remain unproven. The implementation
has not been invoked against the canonical database or a real employer.
