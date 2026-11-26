# FUNCTION-05: Vacancy Detail Verification

Base: `main` `8f49e9c` (Function 02 and Function 04 merged). Tracking: Issue #79. Scope: Function 05 only.
Nothing here opens a browser, touches an application form, uploads a CV, submits, calls a paid API, merges to `main`
or starts Function 06. No live request has been made by this work; the live path exists but is gated by an
owner-quoted digest (below).

## Phase 1: what was reused

| Component | Reused as | Changed |
| --- | --- | --- |
| `http_carrier` (`HttpCarrier`, `ReplayCarrier`, `urllib_transport`) | Every request goes through it: HTTPS, GET only, one allowed host, no redirects (live transport refuses them), no cookies or auth headers, no retry, at least 1 s between requests, request and page caps, an employer stops after its first failure. | No |
| `application_bridge.observe_posting_detail` (W2) | Identity check (posting id and `company.identifier`), `refNumber` requisition id, `active` flag, W2 eligibility conditions. Same detail URL (`api.smartrecruiters.com/v1/companies/<tenant>/postings/<id>`). | No |
| `suitability_evidence` (F04) | `detail_from_smartrecruiters` (identity-bound `VacancyDetail` and SHA-256), `extract_detail_evidence` through `evaluate_report`. | No |
| `suitability.evaluate_report` (F04) | Re-evaluation with the approved criteria, unchanged weights, thresholds, hard blockers and policy. | No |
| `criteria_loader`, `owner_only_files`, `execution_manifest.validate_apply_url` | Approved-digest criteria loading, owner-only output, canonical URL validation. | No |

Integration point: W2 sorts by score (`_order`). Function 05 does **not** use that order. It selects by Function 04's
explicit `priority_rank` (the report's order, including its geography tie-break), because that order is the
reviewed priority. A test (`test_priority_order_wins_over_the_w2_score_sort`) pins the difference.

## Phase 2: what was added

| File | Purpose |
| --- | --- |
| `src/ejs/services/detail_verification.py` | Selection, allowlist, guarded requests, identity/availability/requisition checks, section extraction, F04 re-evaluation, verdicts, Function 06 hand-off. |
| `scripts/function05_detail_verification.py` | `prepare` (offline) and `verify` (`--replay-dir` offline, or `--live` gated by the allowlist digest). Owner-only output outside the repo. |
| `tests/test_function05_detail_verification.py` | 76 offline acceptance tests (core CI job). |

### Flow

1. **Select.** From the verified Function 04 report (schema `EJS-SUITABILITY-1`, `executable`/`submit_authorized` false,
   authority block unchanged, criteria `approved_digest_and_rule_version_match`, `handoff_ready` true, unbroken
   `priority_rank` 1..N): the first up to five `preliminary_high` rows in `priority_rank` order. A high row that cannot be
   bound exactly goes to `selection.unbound` with a reason and is **not** replaced by a lower-priority row.
2. **Bind.** Each target carries ATS (`smartrecruiters`), region (`global`), tenant, posting id, canonical
   `https://jobs.smartrecruiters.com/<tenant>/<id>` URL (validated with `validate_apply_url`, tenant must match the path
   exactly) and the single detail endpoint it may read. Duplicate identities are refused.
3. **Allowlist.** `EJS-DETAIL-ALLOWLIST-1`: targets, request policy (GET, https, one host, `max_requests` = target count,
   1.0 s interval, no redirects, 0 retries, no auth, each URL at most once), source hashes, and
   `approval.allowlist_sha256`. The owner reviews the file and quotes the digest to approve exactly these requests.
4. **Request.** `request_details` makes one carrier call per target in allowlist order. A guard rejects any URL that is
   not the target's allowlisted URL or was already requested (zero requests happen in that case), and the carrier log is
   checked afterwards. Before any request the allowlist is **recomputed** from the Function 04 report and must be identical
   (`ALLOWLIST_NOT_REPRODUCED_FROM_F04_REPORT` otherwise), and the Function 04 report itself must be reproducible from the
   approved criteria (`F04_REPORT_NOT_REPRODUCIBLE`).
5. **Verify.** Identity (id and company identifier must match; optional `postingUrl` must be the same tenant and posting),
   availability (`active` true / false / unknown), requisition id (`refNumber`, W2 rule). Mismatch or unreadable body: the
   body is **not** evaluated.
6. **Extract.** Employer wording only: `jobDescription` lines, `qualifications` lines and, among those, the lines with an
   explicit must/required/mandatory cue and no optional wording. Language, sponsorship, work rights and compensation come
   from the Function 04 extractor (explicit statements; hedged, negated, conditional or absent stays Unknown). Nothing about
   the candidate is compared or inferred (`candidate_qualification_match: not_performed`).
7. **Re-evaluate.** `evaluate_report` over all rows of the Function 04 report (so similarity groups still count) with the
   identity-bound details. Same approved criteria, digest and rule version.
8. **Verdict** per candidate:
   * `confirmed_incompatible`: the posting states a hard blocker (explicit no sponsorship, unrestricted right to work required
     while the candidate is not authorized, unmet mandatory language, junior with both sources), is inactive, or the
     compensation is confirmed below target, or Function 04 classes it `verified_blocked` / `verified_below_threshold`.
   * `suitable`: Function 04 `verified_apply_candidate` **and** identity bound **and** active **and** requisition id present
     **and** no W2 eligibility condition **and** no review flag.
   * `incomplete_evidence`: everything else, with reason codes (request failed or not made, identity mismatch, availability
     or requisition unverified, ambiguous sponsorship, review flags, `f04_verified_needs_review`, ...). Unknown stays Unknown;
     an incomplete candidate is never promoted or excluded.
9. **Hand-off.** `EJS-F06-HANDOFF-1`: non-executable, all authority flags false, `first_suitable_candidate` (or null with a
   named next step), candidates ordered suitable first then by re-evaluated priority, confirmed-incompatible candidates listed
   apart, and the prerequisites Function 06 still needs (owner review, fresh eligibility review, verified candidate facts,
   observed form, execution manifest validation, separate authorization).

### Evidence preservation

`raw/<tenant>__<posting_id>.json` (0600, same layout Function 04's `--details-dir` reads), `evidence-manifest.json`
(URL, observation timestamp UTC, HTTP status, byte count, SHA-256, stored path), the report repeats the SHA-256 and the
request log. Everything lives in the owner-only output directory outside the repository. Nothing private (CV, candidate
facts, form data) is read or written.

## Phase 3: offline acceptance criteria

| # | Criterion | Result | Evidence (`tests/test_function05_detail_verification.py`) |
| --- | --- | --- | --- |
| 1 | Up to five `preliminary_high` rows, Function 04 priority preserved (not W2 score order) | Met | `test_selects_five_...`, `test_priority_order_wins_over_the_w2_score_sort`, fewer-than-five test |
| 2 | Exact ATS, tenant, posting id, canonical URL; unbindable rows reported, not replaced | Met | `test_every_target_is_bound_...`, `test_unbindable_high_row_...` |
| 3 | Explicit, reviewable, digest-bound, non-executable allowlist | Met | `test_allowlist_is_non_executable_reviewable_and_digest_bound` |
| 4 | Active, identity-bound posting with requisition id | Met | `test_active_identity_bound_posting_...` |
| 5 | Inactive posting | Met (confirmed incompatibility) | `test_inactive_posting_...` |
| 6 | Identity mismatch (id, tenant, `postingUrl`, non-object, unreadable, empty) never evaluated or suitable | Met | `test_identity_mismatch_...` (9 cases) |
| 7 | Missing or invalid requisition id never suitable | Met | `test_missing_or_invalid_requisition_id_...`, missing `active` flag |
| 8 | Explicit mandatory eligibility restriction | Met (confirmed incompatibility, quote kept) | `test_explicit_mandatory_eligibility_restriction_...` |
| 9 | Unknown, hedged, conditional or negated sponsorship/work-right wording | Met (incomplete, never incompatible) | `test_unknown_or_ambiguous_sponsorship_...` (5 cases), W2 condition test |
| 10 | HTTP failure (500/404/429/403/oversize), transport exception | Met (one attempt, no retry, employer stopped, rest not requested) | `test_http_failure_...`, `test_transport_exception_...` |
| 11 | Request-budget exhaustion | Met (no extra request) | `test_request_budget_exhaustion_...` |
| 12 | Function 04 re-evaluation, same criteria, inputs untouched | Met (equals a direct `evaluate_report` call) | `test_reevaluation_matches_function_04_...`, reproducibility test |
| 13 | No unapproved target, no duplicate request, no submission authority | Met | tamper cases (10), duplicate/oversize refusal, guard test, hand-off authority tests, source scan, network-denied fixture |
| 14 | Carrier restrictions reused (HTTPS, GET, no redirects, no auth, no retry, >= 1 s, caps, allowed host) | Met | `test_real_carrier_enforces_...`, `test_live_transport_...`, header equality in request test |
| 15 | Live path gated | Met | CLI tests: no digest, wrong digest, empty digest: exit 2, zero requests; approved digest: only allowlisted URLs, once each |
| 16 | Prioritized, non-executable Function 06 hand-off | Met | three hand-off tests |

Validation (offline): 76 Function 05 tests; focused regressions (Function 04, W2 bridge, discovery, expansion live/replay)
356 passed. Full-suite result is in the PR description.

## Phase 4: bounded live pilot (prepared, not executed)

* Maximum **5** GET requests; exactly the allowlisted `https://api.smartrecruiters.com/v1/companies/<tenant>/postings/<id>`
  endpoints; each at most once; no browser, no application form, no CV, no submission.
* The real shortlist is **not known to this work**: the private store (`.ejs-local`) was not readable in this session and
  nothing was bypassed. Per the baseline, the expected shortlist is the five `preliminary_high` rows of the KPMGNederland/NL
  pilot (`KPMGNederland`, SmartRecruiters, region `global`), so the expected request count is **5** (fewer if fewer rows bind).
  Exact posting ids and canonical URLs appear only after step 1 below.

### Step 1: owner generates the sanitized shortlist (offline, no request)

```sh
cd ~/Local/Projeler/EJS/ejs-function-05-detail-verification && PYTHONPATH=src /Users/cagataybuyuk/europe-job-scan/.venv/bin/python \
  scripts/function05_detail_verification.py prepare \
  --f04-report "$HOME/Local/Projeler/EJS/.ejs-local/f04-suitability-20261126-125406/suitability-report.json" \
  --criteria "$HOME/Local/Projeler/EJS/.ejs-local/w1-pilot/criteria.approved.yaml" \
  --expected-criteria-sha256 57faeefb94441abe9f659f8ce636b35c42cbe1b2411e67035ef296d485191f73 \
  --expected-rule-version EJS-W1-PILOT-20261125-v1 \
  --out-dir "$HOME/Local/Projeler/EJS/.ejs-local/f05-allowlist-20261126"
```

It verifies the criteria digest and rule version, checks that the Function 04 report is what those criteria reproduce,
writes `detail-allowlist.json` (owner-only) and prints: `expected_request_count`, `allowlist_sha256`, each target's
`f04_priority_rank`, tenant, posting id, canonical URL and detail URL, and any `unbound` high rows. It makes no network call.
Paste the printed output on Issue #79 for approval.

### Step 2 (only after separate owner approval of that exact output): the bounded read

```sh
cd ~/Local/Projeler/EJS/ejs-function-05-detail-verification && PYTHONPATH=src /Users/cagataybuyuk/europe-job-scan/.venv/bin/python \
  scripts/function05_detail_verification.py verify --live --approve-allowlist-sha256 <allowlist_sha256 the owner approved> \
  --f04-report <same> --criteria <same> --expected-criteria-sha256 <same> --expected-rule-version <same> \
  --allowlist "$HOME/Local/Projeler/EJS/.ejs-local/f05-allowlist-20261126/detail-allowlist.json" \
  --out-dir "$HOME/Local/Projeler/EJS/.ejs-local/f05-detail-20261126"
```

Offline rehearsal with saved bodies: replace `--live --approve-allowlist-sha256 ...` with `--replay-dir <dir of tenant__posting.json>`.
The live command refuses without the exact digest (exit 2, zero requests). It prints verdict counts and the first suitable
candidate identity; the report, hand-off and raw evidence stay in the owner-only directory.

### Stop conditions

Any HTTP error, redirect, 429/403, body above 5 MB, identity or schema surprise stops that employer (no retry). Do not rerun
a failed pilot without a new approval. Function 06 does not start from this hand-off.

## Status

Function 05 offline acceptance: complete. Live pilot: ready, **not approved, not executed**. Real shortlist, real verdict
counts and any first suitable candidate: not available until the owner runs step 1 (and, after approval, step 2).
Employer-confirmed autonomous applications remain 0.
