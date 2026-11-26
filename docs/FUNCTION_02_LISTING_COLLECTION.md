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
| `replay_private_batch(replay=...)` | The durable store accepts a replay callable, so the live path reuses the same owner-only lock, atomic bundle and report publication. |
| `shadow_discovery.py --expansion-live` | Mutually exclusive with `--live`, `--fixture-dir` and `--expansion-fixtures`. Exits with `EXPANSION_LIVE_SCOPE_NOT_APPROVED` before any request while `LIVE_APPROVED_PAIRS` is empty. |
| `build_expansion_report(live_requests=...)` | Marks `transport_mode: live_country_feed`, records the real feed URL as evidence and the per-employer request count of the invocation (`requests_scope: this_invocation`). `execution_authorized` stays `false`. |

Added after owner decisions of 26 Nov 2026 (pilot `KPMGNederland` / `NL`, at most 5 public GET attempts,
failures included, 1 s interval, no redirects, no authentication, no internal postings):

| Area | Change |
| --- | --- |
| `LIVE_MAX_REQUESTS = 5` | Hard per-invocation ceiling. The CLI builds the carrier with it, so failed attempts count and nothing retries. |
| `recheck_first_page` | After a pair completes, offset 0 is read once more. A changed `totalFound` or changed first-page identities is reported as `drift`; an unreadable page as `unreadable`; an exhausted budget as `not_checked`. One request is reserved for it. Any `drift` or `unreadable` check makes the run exit `1`. |
| Pagination drift inside a run | Already fail-closed: changed total, wrong offset echo, repeated or overlapping IDs reject the page and block the tenant. |
| `snapshot_consistency` | Every live report states `atomic_snapshot: false`. Completeness means observed pages and totals only. A stable recheck does not prove unobserved pages did not move. |
| `request_log` | Each report lists URL, status and size of every request of the invocation. |
| `project_for_application_bridge` | Minimal verified view for the unchanged W2 bridge (`schema`, `listings_kept`, `vacancies`, plus provenance fields). Refuses synthetic or executable reports, failed pairs, non-stable rechecks and count mismatches with `EXPANSION_PROJECTION_REFUSED`. |

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
| 1 | Approved exact employer/country scope | **Open (owner)** | `LIVE_APPROVED_PAIRS` is empty by design. |
| 2 | Verified country filter, pagination and identity semantics | **Partial** | Documentation confirmed. Live behaviour unverified. Fail-closed tests: `tests/test_expansion_live.py` (drift, wrong country, wrong tenant, offset, total, duplicates). |
| 3 | Bounded allowlisted read-only collection on the existing engine | **Implemented, not run live** | GET-only carrier, host allowlist, 5/60 ceilings and 1 s floor reused. Tests assert no request outside the approved set and no overrun. |
| 4 | Real fresh normalized listings, measured coverage and completeness | **Open** | Needs criterion 1. Report already emits per-pair coverage and the six-field completeness ratio. |
| 5 | No duplicate identities | **Met (synthetic)** | Cross-country and cross-batch dedup tests. Not measured on a real feed. |
| 6 | Durable restart and per-employer failure isolation | **Met (provider-shaped synthetic)** | HTTP 503, malformed body and process-kill tests. Real-feed evidence is open. |
| 7 | Downstream-compatible output, distinguished from synthetic | **Met, with owner decision** | Report says `live_country_feed`. The W2 bridge still rejects `EJS-EXPANSION-SHADOW-REPORT-1` on purpose; an explicit projection to `EJS-SHADOW-REPORT-2` is accepted and tested. |
| 8 | Repeatable daily collection without application writes | **Met (synthetic)** | One directory per day. Earlier days stay byte-identical. Re-initializing a used day is refused. |

## Daily collection and recovery

Initialize one directory per day (for example `…/2026-11-27`) with `--expansion-live --expansion-init`.
After an interruption rerun the same command without `--expansion-init`:

* A kill before the bundle commit leaves no bundle. Initialize again; pages are idempotent GETs.
* A kill or failure after the commit resumes from the saved cursors. Completed pairs are not requested again.
* A failed tenant stays blocked for the day. Other tenants continue. Exit code `1` means a failure, `3` incomplete, `0` complete.
* The next day uses a new directory. Compare `vacancies[].posting_id` across the two reports to find new listings.

Request counts in a report describe the latest invocation only.

## Blockers and owner decisions

1. **Scope approval.** Name the exact tenant/country pairs, as a prefix of `COUNTRIES` per tenant, and the request ceilings. The current four-employer gate lists only `Rituals1` for SmartRecruiters, which is not in the 27-employer research scope. Verifying the filter therefore needs an explicit read-only approval; this work made no live request.
2. **Live verification run.** A small approved pilot (one tenant, one country, up to five requests) should confirm the lowercase code, filter exactness, total stability and the response shape before widening. Record only status codes, counts and execution IDs.
3. **Bridge intake.** Decided: the W2 contract is unchanged and `project_for_application_bridge` is the minimal verified projection.
4. **Same-total drift.** Only the first page is re-read. Drift deeper in the feed is not observable inside the request budget, so no atomic or full-snapshot completeness is ever claimed.
5. **Release.** Merge into `main` needs release approval. Exact-head CI on the pushed branch is not yet observed.

## Validation

Targeted: `PYTHONPATH=src:. python -m pytest -q tests/test_expansion_live.py tests/test_expansion_replay.py tests/test_expansion_replay_store.py tests/test_expansion_shadow_integration.py tests/test_application_bridge.py`.
Run with network access denied. `tests/test_expansion_live.py` is in the core CI job.
