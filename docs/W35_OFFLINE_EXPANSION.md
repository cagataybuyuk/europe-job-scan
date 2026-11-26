# W3.5 employer expansion scope and offline replay

The owner-reviewed W3.4 SmartRecruiters employer research is dated 2026-11-26.
Independent QA compared the 27 employer names, exact tenant IDs, tiers, nine
country columns and all 243 counts against both
`ejs-w34-employer-research.md` (coverage matrix) and
`ejs-w34-employers.csv`. The counts sum to 4,508 reported historical listings.
The scope includes 121 nonzero and 122 zero-count pairs. Historical counts and
research tiers establish scope and ordering only. The owner-maintained
Employer Watchlist and the four-employer live pilot have separate scope.

The prior local W3.5 worktree was clean at `ee232ce`, an ancestor of PR #126.
It contained no unique commits or source changes to consolidate. QA applied its
changes in a detached worktree at `fcadb0f`.

## Replay contract

Call `initial_checkpoint()` and `replay_batch(checkpoint, fixtures)` with a plain
in-memory dict keyed by (exact tenant, country code, offset). Each synthetic
page dict must repeat `tenant` and `country`, including for empty pages. These are
fixture metadata fields supplied by the offline fixture author.

A page commits only after the scope, pagination and every row validate. Each row
supplies the exact publisher tenant, country and opaque posting ID, plus a
printable title and an optional printable city. The existing SmartRecruiters
normalizer constructs the canonical URL without copying source URLs or queries.
Changed totals, wrong offsets, missing pages, nonprogress and repeated IDs
within a country reject the complete page. Detecting changed unseen IDs with the
same total and valid offsets requires additional feed evidence.

Each batch consumes at most five pages per employer and 60 pages in total,
including failures. Budget exhaustion leaves pending cursors. An error retains
prior progress and blocks later pages for that tenant. Other tenants may proceed.
A pair completes only when an accepted page reaches the declared total. Exact
(tenant, posting ID) deduplication keeps the first occurrence across countries
and batches while preserving per-country cursor coverage.

## Durable synthetic batches

On macOS and Linux, call
`scripts.expansion_replay_store.replay_private_batch(directory, fixtures, initialize=True)`
once for a new bundle. For later batches use the same directory with the default
`initialize=False`. The directory must be absolute, canonical, owner-only and
outside Git worktrees and `control` paths, with an owner-controlled parent. The
store checks effective ACLs and rejects symlinks, nonregular files, hard links
and insecure permissions. Parallel processes fail on the private advisory lock.

The owner-only `expansion-replay.json` bundle holds all cursors and the
cumulative deduplicated normalized listings. Resume checks that the identities
and countries in the bundle agree with accepted cursor progress. File and
directory synchronization plus atomic replacement publish both together. On
failure before replacement, the earlier bundle remains available. If an error
occurs after replacement, reload the bundle to determine which batch committed.
The 32 MB bound applies to both reading and writing. Owner-only storage assumes
the local owner is trusted and provides no authentication against owner edits.
The bundle schema is specific to this offline replay.

## Validation and release gates

Core CI runs both synthetic suites. The targeted local command is
`python -m pytest -q tests/test_expansion_replay.py tests/test_expansion_replay_store.py tests/unit/test_ejs_phase1b_discovery.py tests/unit/test_ejs_shadow_phaseA.py tests/unit/test_watchlist_coverage.py`.
Run with network access denied. No candidate data or live ATS access is needed.

Before live expansion, separately review the provider's country-filter and
pagination consistency semantics, the approved scope and rate floor, and how
the checkpoint and output integrate with the shadow report, evidence and
deduplication pipeline. The current four-employer live gate stays in force.
