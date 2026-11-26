# W3.5 — explicit employer expansion scope and offline replay

## Source and scope

Source: owner-reviewed EJS-W3.4 SmartRecruiters Employer Expansion Research,
dated 2026-11-26, priority-market coverage matrix (27 employers). The matrix
was available in the project's Library as ejs-w34-employer-research.md.
All 27 exact tenant identifiers, nine country columns and per-country counts
were transcribed; their sums were reconciled to the reported 4,508 historical
priority-market listings. This is not a new live verification. The original
research CSV was not available in this session; no CSV values were guessed.

The expansion scope is separate from the owner-maintained Employer Watchlist
and the existing four-pilot live gate. The 27 x 9 explicit pairs include all
122 pairs with historical zero counts: a prior zero is not a permanent filter.
The tiers are research prioritization only; no employer-level exclusion based
on language, sponsorship, job family or volume is applied.

## Offline replay contract

- Explicitly call initial_checkpoint() and replay_batch(checkpoint, fixtures).
  Fixtures are in-memory synthetic dictionaries keyed by
  (exact tenant, country code, offset); no filesystem or transport is called.
- Existing SmartRecruiters normalization and 100-item page ceiling are reused.
  Every item must have the exact requested country, a valid posting identity
  and valid text. A changed total, wrong offset, missing page, no progress,
  invalid row or repeated ID within a country fails that page atomically.
- A run may consume at most five pages per employer and 60 in total,
  including failed pages. Budget exhaustion leaves pending cursors; no
  automatic retries follow an error. An errored tenant is blocked until review.
- Dedup uses exact provider tenant/posting identity across countries and prior
  batches. The per-country cursor still records all seen IDs for coverage.
- Pending does not mean complete; only an accepted page reaching the feed
  total completes a pair. Errored tenants may have remaining pending pairs.
- The checkpoint is an in-memory immutable value. It is not a durable,
  authenticated cursor; storing and restoring it across process restarts is
  not part of this increment. Listings and checkpoint are returned together;
  a later private publisher must commit them atomically to avoid lost output.
- No network request, report write, actual candidate data, ATS application
  interaction, runtime, STOP, ledger or production policy change is included.

## Validation and remaining integration

Run: python -m pytest -q tests/test_expansion_replay.py
The existing core CI list includes this suite. No live endpoint is exercised.
Before any separately authorized live expansion, review the real country
filter semantics, durable private checkpoint/report publishing, transport
rate floor, explicit approved scope, and integration with existing shadow
report, evidence and dedup behavior. Do not loosen the existing pilot gate.
The local W3.5 worktree is not accessible from this session; reconcile its
existing changes before merging any overlapping implementation.
