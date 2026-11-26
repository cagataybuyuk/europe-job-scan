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

## Shadow discovery CLI

Select `--expansion-fixtures` to run one offline batch through the existing
shadow discovery CLI. Supply a synthetic watchlist using the existing
`Employer,Primary Market,Reference URL` columns. It must contain exactly
the 27 research employers with their exact SmartRecruiters tenants. Supply
synthetic versioned criteria through `--criteria`. The country scope comes
from the nine research country codes. Market labels and historical counts do
not filter it.

The fixture must be a local JSON array of page records. A record's outer
tenant, country and offset select its page. The payload repeats the tenant,
country and offset so the replay can detect a misplaced page. For example, an
empty Netherlands page is represented as follows.

```json
[
  {
    "tenant": "KPMGNederland",
    "country": "NL",
    "offset": 0,
    "page": {
      "tenant": "KPMGNederland",
      "country": "NL",
      "offset": 0,
      "limit": 100,
      "totalFound": 0,
      "content": []
    }
  }
]
```

Include a page for every requested offset of every pair. Each posting in
`content` supplies `id`, `name`, `company.identifier`, `location.country`
and optionally `location.city`. Use synthetic values throughout. The test
`test_all_243_pairs_and_identical_final_reports_under_different_batch_sizes`
constructs a complete snapshot with empty, multi-page and cross-country
duplicate cases.

Set `EJS_SYNTHETIC` to an absolute, canonical, owner-controlled directory
outside the repository. Place your synthetic inputs in that directory.

```sh
PYTHONPATH=src python scripts/shadow_discovery.py \
  --watchlist "$EJS_SYNTHETIC/scope.csv" \
  --criteria "$EJS_SYNTHETIC/criteria.yaml" \
  --expansion-fixtures "$EJS_SYNTHETIC/pages.json" \
  --out-dir "$EJS_SYNTHETIC/expansion" --expansion-init
```

Resume in the same output directory by omitting `--expansion-init`. Each
invocation keeps the 60-page overall and five-page per-employer ceilings.
`--expansion-max-pages` can lower the overall ceiling. The CLI binds the
bundle to the fixture bytes, parsed criteria and validated watchlist mapping.
Use a new directory for a corrected fixture snapshot or for a CLI run after
using the unbound replay API. The selector is
mutually exclusive with `--live` and `--fixture-dir`. `--sheet-labels` is
unavailable in this synthetic mode.

The CLI emits these coverage states for each employer and country pair.

| State | Meaning |
| --- | --- |
| `completed` | An accepted page reached the declared total, which can be zero. |
| `incomplete` | The pair still has unprocessed pages. Resume to continue. |
| `failed` | A missing or invalid page halted this pair and preserved its accepted progress. |
| `blocked` | An earlier country for the same employer failed. |

Exit `0` means all pairs completed. Exit `3` means coverage is incomplete.
Exit `1` means at least one pair failed. Review coverage on exit `1` and
resume if other employers still have incomplete pairs. The failed employer
stays blocked for this snapshot. Exit `2` indicates invalid input or a local
storage or publication error.

The owner-only bundle is the source of truth for restart. The workflow
publishes cumulative `expansion-report.json` and `expansion-report.md` under
the same process lock. If a report write is interrupted after the bundle
commit, resume without `--expansion-init` to rebuild the reports from durable
state. The two reports are replaceable views and may briefly reflect different
checkpoints after an interruption.

The JSON report keeps the shared normalized vacancy fields, exact identity
deduplication, similarity key and criteria evaluation. It reports the
synthetic countries in which each posting was observed and records zero network
requests. Vacancy freshness, sponsorship eligibility and application readiness
remain unverified. The expansion-specific report schema is rejected by the W2
application bridge. The report grants no execution authority.

## Validation and release gates

Core CI runs the replay, store and shadow integration suites. The targeted local command is
`PYTHONPATH=src python -m pytest -q tests/test_expansion_replay.py tests/test_expansion_replay_store.py tests/test_expansion_shadow_integration.py tests/unit/test_ejs_phase1b_discovery.py tests/unit/test_ejs_shadow_phaseA.py tests/unit/test_watchlist_coverage.py tests/test_application_bridge.py tests/test_application_batch.py`.
Run with network access denied. No candidate data or live ATS access is needed.

Before enabling live expansion, verify the provider's country-filter contract,
country coverage and pagination consistency on approved read-only targets.
Review the exact tenant and country allowlist, request ceilings, rate floor
and live checkpoint provenance and recovery with real feed evidence. Keep
application eligibility, work rights, readiness and execution behind their
existing independent gates. The current four-employer live gate stays in force.
