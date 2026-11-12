# Reviewed different-target TEST scope

The 12 November request selects another vacancy for testing. It does not reopen
the consumed Version 1 first-application slot. `review_next_test_scope` accepts
only metadata for a different reviewed native SmartRecruiters target. Its result
has browser, field write, CV upload, Next, final submit and ledger creation all
false. It performs no network, file, ledger, browser or Sheet operation and is
not wired into the executable command.

The independent reviewer supplies exact SHA-256 pins for raw scope, manifest and
fact JSON bytes plus the source commit. The scope binds the complete target,
candidate profile version, approved CV hash, reviewer, manifest/fact hashes and
a canonical current-history fingerprint. The review expires within 24 hours.
Unknown keys (including any extra grant, path, ledger or execute flag), stale or
non-UTC review, changed pins, target/profile/CV/reviewer drift and a missing native
entry decision stop. Existing private-file/input validation, fresh vacancy fit,
actual CV bytes and endpoint review remain separate caller obligations.

History comes from the trusted caller's current canonical read. The existing
first-ledger snapshot must remain Confirmed and match REF7089X; history must
include its exact original target once. A matching posting, publication or
company/requisition in any reserved, uncertain or confirmed record stops the new
test target. New execution/profile versions and changed public slugs cannot
reopen a consumed vacancy. The history digest is not source authentication or a
concurrency lock; this module cannot make a caller-supplied history authoritative.

The scope schema is `EJS-REVIEWED-NEXT-TEST-SCOPE-1`, `mode=review_only`, decision
`USER-MVP-TEST-TARGET-20261112`. It deliberately contains no operation authority.
The existing CLI remains pinned to the already consumed first target.

Before a later real application, a separate exact-target operation grant and
independent runtime review are required. A proposed canonical history migration
must retain the original first ledger unchanged, bind its consumed outcome and
all historical target identities, serialize preparation/reservation, and consume
the new explicitly approved scope once even on uncertainty. Do not create a
replacement ledger, reset the first slot or derive a new budget from a worktree,
manifest/profile version, date, target or arbitrary command path. No migration,
new live budget or general rollout is implemented or authorized by this module.
