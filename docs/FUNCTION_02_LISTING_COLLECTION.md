# FUNCTION-02: Listing Collection

Base: consolidation PR #132 (`2f898d4`). Tracking: Issue #79. Scope: function 02 only. Nothing here
submits, writes forms, uploads candidate data or touches runtime, STOP, ledger or policy files.

## What changed

| Area | Change |
| --- | --- |
| `expansion_replay.live_page_url` | Builds the documented public feed URL: `/v1/companies/{tenant}/postings?country=<lowercase>&offset=N&limit=100`. Only research-scope pairs are accepted. |
| `expansion_replay.live_page` | Converts a provider-shaped response into the existing replay page shape. The feed carries no tenant or country metadata and reports lowercase country codes, so tenant and country come from the request. A posting is relabelled only when it equals the requested country or tenant ignoring case. Everything else is rejected by the unchanged `_page` validator. The provider's offset, limit and total echo are kept and checked. |
| `expansion_replay.live_replay_batch` | Same validation, 5/60 ceilings, tenant isolation and checkpoint semantics as `replay_batch` (both share `_replay`). Reads through an injected `HttpCarrier`. Requests only pairs in `LIVE_APPROVED_PAIRS`. A carrier that is tighter than the batch ceilings stops the batch without failing any pair. |
| `LIVE_APPROVED_PAIRS` | **Pilot pair only:** `KPMGNederland` / `NL` (owner decision 26 Nov 2026). Any other pair needs a new reviewed approval. Per tenant the approved set must be a prefix of `COUNTRIES`, because checkpoint validation requires countries of one tenant to advance in order. Other shapes raise `EXPANSION_LIVE_SCOPE_INVALID`. |
| `replay_private_batch(live=...)` | The durable store accepts a live callable, so the live path reuses the same owner-only lock, atomic bundle and report publication. It also persists request accounting and recheck evidence (see F02-01 below). |
| `shadow_discovery.py --expansion-live` | Mutually exclusive with `--live`, `--fixture-dir` and `--expansion-fixtures`. Reads only `LIVE_APPROVED_PAIRS` (the pilot pair). With an empty set it exits with `EXPANSION_LIVE_SCOPE_NOT_APPROVED` before any request. |
| `build_expansion_report(evidence=..., approved=...)` | Marks `transport_mode: live_country_feed`, records the real feed URL as evidence and builds request accounting and rechecks from the bundle's durable evidence only. `execution_authorized` stays `false`. |

Added after owner decisions of 26 Nov 2026 (pilot `KPMGNederland` / `NL`, at most 5 public GET attempts,
failures included, 1 s interval, no redirects, no authentication, no internal postings):

| Area | Change |
| --- | --- |
| `LIVE_MAX_REQUESTS = 5` | Hard per-invocation ceiling. The CLI builds the carrier from the invocation's durable reservation, so failed attempts count and nothing retries. |
| `recheck_first_page` | After a pair completes, offset 0 is read once more. A changed `totalFound` or changed first-page identities is reported as `drift`; an unreadable page as `unreadable`; an exhausted budget as `not_checked`. One request is reserved for it. Any `drift` or `unreadable` check makes the run exit `1`. |
| Pagination drift inside a run | Already fail-closed: changed total, wrong offset echo, repeated or overlapping IDs reject the page and block the tenant. |
| `snapshot_consistency` | Every live report states `atomic_snapshot: false`. Completeness means observed pages and totals only. A stable recheck does not prove unobserved pages did not move. |
| `request_log` | Each report lists URL, status, size and invocation number of every request ever made for the bundle. |
| `project_for_application_bridge` | Minimal view for the unchanged W2 bridge (`schema`, `listings_kept`, `vacancies`, plus provenance fields). Row-level: see F02-02 below. Refuses synthetic or executable reports, atomic-snapshot claims and count mismatches with `EXPANSION_PROJECTION_REFUSED`. |

Added after the independent review of PR #133 (head `0d92791`):

| Finding | Change |
| --- | --- |
| **F02-01** durable evidence | The private bundle gains an `evidence` section, written in the same atomic replace as the checkpoint and listings. It holds every invocation (reservation, status, each request's URL/status/size) and an append-only list of per-pair first-page rechecks. A report is always rebuilt from it, so a zero-request resume keeps the request log, accounting and recheck results. The effective recheck of a pair is its worst recorded result: `drift` and `unreadable` are sticky and a later `stable` never hides them. A resume exits `1` while drift stands. A pair left `not_checked` (budget exhausted) is rechecked by a later invocation, and the history keeps both entries. |
| **F02-01** budget and in-flight attempts | The 5-attempt budget is per bundle (one day's snapshot) and cumulative across invocations (`LIVE_BUNDLE_MAX_REQUESTS`); the per-invocation ceiling stays 5. Before any request the invocation's reservation is published durably as `in_flight`. If the process dies, the next run records that invocation as `interrupted_unverified` and charges the whole reservation, so unrecorded attempts can never exceed the budget. With no work left, no reservation is made and the bundle is not rewritten. A live bundle without valid evidence (for example one written before this change) is refused before any request. |
| **F02-02** projection | Rows are projected only when every country pair they were observed in is `completed` and has a stable recheck. Rows from pending, failed, blocked, unapproved or drifting pairs, and rows that were also seen in such a pair, are excluded and counted in `excluded_rows` with a reason. Previously only completed pairs were checked, so accepted rows of a pending pair could pass. |
| Coverage | `coverage.status` and `coverage.counts` are measured over the approved scope; other pairs are `not_approved` and listed in `coverage.research_scope` (`total_pairs`, `approved_pairs`, `completed_pairs`, `not_approved_pairs`, `research_coverage_complete`). A finished pilot is therefore `completed`, and research coverage is never overstated. Synthetic reports keep their previous shape. |

Preserved unchanged: normalization (`parse_smartrecruiters`), exact `(tenant, posting_id)` deduplication,
cursor checkpoint, private bundle, report builder, the four-employer `--live` gate, and the HTTP carrier's
GET-only, no-redirect, 1 s floor, 5/60 limits and host allowlist.

## Differences between synthetic replay and a real collection

1. Real responses have no `tenant`/`country`/`offset` envelope. Handled by `live_page`.
2. Real country codes are lowercase (`nl`); replay fixtures used uppercase. Handled by case-insensitive match, then canonical uppercase.
3. A real run needs the request URL to carry `country`. Replay never built URLs.
4. A real transport fails (HTTP status, malformed body, timeout). Each of these fails only that tenant and keeps accepted progress.
5. Real runs span days. A synthetic snapshot is one-shot, so daily collection uses one private directory per day (see below).

## Provider contract

Verified from SmartRecruiters' published reference (Posting API, "Lists active postings published by given
company"): `GET /v1/companies/{companyIdentifier}/postings` accepts `country` (country-code filter on the
location object), `offset` and `limit` (maximum page size 100); the `ListResult` wrapper returns `offset`,
`limit` and `totalFound` (total without limit and offset). Source pages looked about a year old.

**Not verified against the live service (requires approved read-only requests):** that the documented
lowercase code is required or merely accepted, that the filter is exact for postings with several or remote
locations, that `totalFound` stays consistent while paging, and that ordering is stable. The code is built to
fail closed on every one of these: a posting outside the requested country, a changed total, a wrong
offset or a repeated ID rejects the page and blocks that tenant.
A same-total change of unseen IDs between pages is still undetectable without feed evidence.

## Completion criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Approved exact employer/country scope | **Pilot approved only** | `KPMGNederland` / `NL`. Remaining 242 pairs are reported as `not_approved`, separate from coverage of the approved scope. |
| 2 | Verified country filter, pagination and identity semantics | **Verified for the pilot pair; not for others** | Documentation confirmed; one live pair observed (see pilot evidence). Fail-closed tests: `tests/test_expansion_live.py` (drift, wrong country, wrong tenant, offset, total, duplicates). |
| 3 | Bounded allowlisted read-only collection on the existing engine | **Run once live (3 requests)** | GET-only carrier, host allowlist, 5/60 ceilings and 1 s floor reused. Tests assert no request outside the approved set and no overrun. |
| 4 | Real fresh normalized listings, measured coverage and completeness | **Partial** | 197 listings, six-field ratio 1.0, one of 243 pairs. Freshness of each vacancy is unverified. |
| 5 | No duplicate identities | **Met (synthetic)** | Cross-country and cross-batch dedup tests. Not measured on a real feed. |
| 6 | Durable restart and per-employer failure isolation | **Met (provider-shaped synthetic)** | HTTP 503, malformed body, in-flight kill, resume with preserved evidence, sticky drift and cumulative budget tests. A recovery after a real interruption is not exercised live. |
| 7 | Downstream-compatible output, distinguished from synthetic | **Met** | Report says `live_country_feed`. The W2 bridge is unchanged and still rejects `EJS-EXPANSION-SHADOW-REPORT-1`; the row-level verified projection to `EJS-SHADOW-REPORT-2` is accepted and tested. |
| 8 | Repeatable daily collection without application writes | **Met (synthetic)** | One directory per day. Earlier days stay byte-identical. Re-initializing a used day is refused. |

## Pilot evidence (26 Nov 2026, `KPMGNederland` / `NL`)

One invocation of `--expansion-live --expansion-init --expansion-max-pages 5` from head `01240f9`.
Output stayed in an owner-only directory outside the repository; no candidate data was involved.

* Requests: **3** of 5 (all HTTP 200, no retry, no redirect): `offset=0` (259,658 bytes), `offset=100` (257,057 bytes) and one first-page recheck `offset=0` (259,658 bytes, identical size).
* Pagination: `totalFound` 197 on both pages, offsets echoed 0 and 100, 100 + 97 postings, no overlap, no duplicate ID.
* Result: 197 unique listings, 0 duplicates, 0 unkeyed, all six fields present, every posting `location.country == nl` and `company.identifier == KPMGNederland`. This equals the historical W3.4 count of 197, which is consistent but is not evidence of completeness.
* Drift: first-page recheck `stable`. This does not make the read an atomic snapshot (`atomic_snapshot: false`).
* Output: all 197 rows are `needs_user` / `vacancy_evidence_missing`; no eligibility is asserted. The verified projection passed the unchanged W2 bridge: 197 candidates, all `missing_evidence`, `executable: false`, `submit_authorized: false`.
* Recovery (as run, before F02-01): a second invocation without `--expansion-init` made 0 requests and left the bundle byte-identical, but rebuilt the report without that invocation's request log and recheck. F02-01 fixes this: the evidence now lives in the bundle. The pilot output itself was written before the fix and has no `evidence` section, so the current CLI refuses to resume it; it is not re-run and it is not needed for later days.

## Daily collection and recovery

Initialize one directory per day (for example `…/2026-11-27`) with `--expansion-live --expansion-init`. Each day
has its own bundle and its own 5-attempt budget. After an interruption rerun the same command without
`--expansion-init`:

* A kill before the first reservation leaves no bundle. Initialize again.
* A kill during an invocation leaves a bundle with an `in_flight` reservation. The next run charges it in full
  (`interrupted_unverified`), keeps all validated progress and uses only what is left of the budget.
* A kill or failure after a commit resumes from the saved cursors. Completed pairs are not requested again.
* A failed tenant stays blocked for the day. Other tenants continue.
* Exit codes: `1` a failed pair or standing drift, `3` approved scope incomplete, `4` approved scope collected but
  acceptance not ready (a completed non-empty pair has no stable first-page recheck, for example because the budget
  ran out), `0` approved scope collected and verified. `0` says nothing about the 242 unapproved pairs.
  The report's `acceptance` section states `collection_complete`, `verification_complete`, `unverified_pairs` and `ready`.
* The next day uses a new directory. Compare `vacancies[].posting_id` across the two reports to find new listings.

Request counts in a report are cumulative for the bundle; per-invocation counts are in `request_accounting.invocations`.

## Blockers and owner decisions

1. **Scope beyond the pilot.** Only `KPMGNederland` / `NL` is approved. Any further pair needs an owner decision naming the pairs (per tenant a prefix of `COUNTRIES`), the per-invocation ceiling and the daily bundle budget (currently 5 attempts).
2. **Remaining live unknowns.** One pair was observed. Whether the lowercase code is required or only accepted, filter exactness for multi-location or remote postings, and total stability across tenants are not established.
3. **Bridge intake.** Decided: the W2 contract is unchanged and `project_for_application_bridge` is the minimal verified projection.
4. **Same-total drift.** Only the first page is re-read. Drift deeper in the feed is not observable inside the request budget, so no atomic or full-snapshot completeness is ever claimed.
5. **Vacancy freshness and suitability.** Whether a listing is still open, and eligibility, belong to later function gates.
6. **Release.** Merge into `main` needs release approval. Exact-head CI is tracked on PR #133.

## Final bounded live acceptance (needs separate owner authorization; not executed)

Scope: `KPMGNederland` / `NL` only, one new private bundle, **at most 5 GET attempts in total across all
invocations** (the bundle budget; the sequence below needs 3 for a feed of about 200 listings), 1 s minimum interval,
no redirects, no authentication, no internal postings, no deliberate failures. Run from this worktree at the head
of PR #133. Use one controlled output directory; a new directory starts a new budget, so do not create another
one to continue. The historical pilot bundle is incompatible with the current format by design and is retained.

```sh
cd ~/Local/Projeler/EJS/ejs-function-02-listing-collection
git fetch -q origin && git status -sb && git rev-parse HEAD      # must equal the head of PR #133
PY=/Users/cagataybuyuk/europe-job-scan/.venv/bin/python
export IN="$HOME/Local/Projeler/EJS/.ejs-local/f02-acceptance-inputs"
export OUT="$HOME/Local/Projeler/EJS/.ejs-local/f02-acceptance-$(date +%Y%m%d)"   # must not exist yet

# 0. Offline: private inputs (public tenant names and synthetic criteria only)
mkdir -m 700 "$IN" && PYTHONPATH=src:. $PY - <<'PYEOF'
import os
from pathlib import Path
from ejs.services.expansion_replay import RESEARCH
from tests.unit.phase1a_support import CRITERIA_YAML
d = Path(os.environ["IN"])
(d / "scope.csv").write_text("Employer,Primary Market,Reference URL\n" + "".join(
    f"{n},Public research scope,https://jobs.smartrecruiters.com/{t}\n" for n, t, _, _ in RESEARCH), encoding="utf-8")
(d / "criteria.yaml").write_text(CRITERIA_YAML, encoding="utf-8")
PYEOF

RUN="PYTHONPATH=src $PY scripts/shadow_discovery.py --watchlist $IN/scope.csv --criteria $IN/criteria.yaml --out-dir $OUT --expansion-live"

# 1. First invocation, cap 2 (one page; the pair stays pending). Expected exit 3.
eval "$RUN --expansion-init --expansion-max-pages 2"; echo "exit=$?"
# 2. Resume, cap 3 (second page and the first-page recheck). Expected exit 0 and acceptance=ready.
eval "$RUN --expansion-max-pages 3"; echo "exit=$?"
# 3. Zero-request resume. Expected exit 0, requests unchanged, no new invocation.
eval "$RUN --expansion-max-pages 3"; echo "exit=$?"
# 4. Offline read-only verification of the durable evidence and the W2 projection. Expected exit 0.
PYTHONPATH=src $PY scripts/function02_acceptance_check.py --bundle-dir "$OUT" --expect-invocations 2; echo "exit=$?"
```

Expected evidence: 3 requests, all HTTP 200 on `country=nl` URLs, two invocations plus no third, stable recheck,
`acceptance.ready: true`, every listing unique and equal to the declared total, projection excludes no row, W2
candidates `missing_evidence` and non-executable. The check prints counts and status codes only.

Stop and report, without retrying, when: any exit code differs from the expected one; any request is not HTTP 200;
exit `1` (failed pair or drift); exit `4` after step 2; the feed total needs more pages than the budget allows
(exit `3` after step 2); the check script prints any `FAIL`. Do not start a second bundle to finish the work. A true
multi-day acceptance needs a next-day run and is not claimed by this test.

## Validation

Targeted: `PYTHONPATH=src:. python -m pytest -q tests/test_expansion_live.py tests/test_expansion_replay.py tests/test_expansion_replay_store.py tests/test_expansion_shadow_integration.py tests/test_application_bridge.py`.
Run with network access denied. `tests/test_expansion_live.py` is in the core CI job.


## Version 1 bounded pilot: Version 1 Netherlands / Ireland

Owner approval 26 Nov 2026: exactly Version 1 NL and IE, maximum five public GET attempts total in a new
private bundle (including first-page rechecks). Select explicitly with `--expansion-live --expansion-scope version1-nl-ie`.
This is a separate scope, not the union with the legacy KPMG scope. The original KPMG default and existing bundle
input digest are unchanged; resuming with a different scope is refused. No KPMG request, authentication, redirects,
automatic retries, candidate data, application forms or submission. A single invocation with `--expansion-init
--expansion-max-pages 5` should collect both country pages and recheck non-empty pairs within budget if each fits
one page. Historical counts are not current counts; stop and inspect any nonzero exit without starting another
bundle or repeating a request. Use `scripts/function02_acceptance_check.py --scope version1-nl-ie
--bundle-dir <new private directory> --expect-invocations 1` for offline acceptance. No other pair is approved by this
change. No F05 detail request is authorized by the F02 listing approval.


### Rejected page diagnostics

For live collection only, the cursor and report retain a fixed allowlisted reason such as
`page_review_required:country_mismatch`, `page_review_required:tenant_mismatch`, or
`page_review_required:invalid_pagination`. First-page recheck failures retain the same safe suffix where known.
Unknown exception details remain generic. No response text, arbitrary exception string, or posting content is
persisted by this diagnostic change; rejected raw bodies are still not retained. Existing legacy generic errors
remain readable; synthetic error behavior, fail-closed checks, tenant blocking, budget accounting and no-retry
behavior are unchanged. This cannot reconstruct the previously rejected ServiceNow response. That pilot remains
parked on #137 and is not included in this branch.
