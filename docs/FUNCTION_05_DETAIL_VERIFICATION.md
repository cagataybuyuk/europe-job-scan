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
| `tests/test_function05_detail_verification.py` | 98 offline acceptance tests (core CI job). |

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
   (`ALLOWLIST_NOT_REPRODUCED_FROM_F04_REPORT` otherwise). Live runs also need the single-use budget below. After each
   response, an unreadable, oversized, identity-mismatched or `postingUrl`-mismatched body stops that employer **at once**;
   its remaining targets are recorded `employer_stopped_invalid_response`, not attempted.
5. **Verify.** Identity (id and company identifier must match; optional `postingUrl` must be the same tenant and posting),
   availability (`active` true / false / unknown), requisition id (`refNumber`, W2 rule). Mismatch or unreadable body: the
   body is **not** evaluated and the employer stops (step 4).
6. **Extract.** Employer wording only: `jobDescription` lines, `qualifications` lines and, among those, the lines with an
   explicit must/required/mandatory cue and no optional wording. Language, sponsorship, work rights and compensation come
   from the Function 04 extractor (explicit statements; hedged, negated, conditional or absent stays Unknown). Nothing about
   the candidate is compared or inferred (`candidate_qualification_match: not_performed`).
7. **Re-evaluate.** `evaluate_report` over all rows of the Function 04 report (so similarity groups still count) with the
   identity-bound details. Same approved criteria, digest and rule version.
8. **Verdict** per candidate:
   * `confirmed_incompatible`: only an approved K2 hard blocker stated in the posting (explicit no sponsorship, unrestricted
     right to work required while the candidate is not authorized, unmet mandatory language, junior with both sources), an
     inactive posting, or the approved engine's own `verified_blocked` / `verified_below_threshold` class. **No new hard
     blocker:** below-target compensation is scored by the engine (rating 0) and shown as the note
     `compensation_below_target_scored_not_a_blocker`, never an exclusion. **Contradictory** employer statements
     (e.g. refusal and offer of sponsorship in the same posting) never become a confirmed exclusion; they are
     `incomplete_evidence` with `contradictory_evidence_requires_review`. A closed posting stays confirmed.
   * `candidate_for_review` (provisional): Function 04 `verified_apply_candidate` **and** identity bound **and** active
     **and** requisition id present **and** no W2 eligibility condition **and** no review flag. **Never "suitable":**
     mandatory qualifications are not checked against verified candidate facts in Function 05
     (`mandatory_qualifications_not_checked_against_verified_candidate_facts`); that check is a Function 06 prerequisite and
     the employer's mandatory qualification lines are carried in the hand-off for it.
   * `incomplete_evidence`: everything else, with reason codes (request failed or not made, identity mismatch, availability
     or requisition unverified, ambiguous sponsorship, contradictory evidence, review flags, ...). Unknown stays Unknown; an
     incomplete candidate is never promoted or excluded.
9. **Hand-off.** `EJS-F06-HANDOFF-1`: non-executable, all authority flags false, `first_candidate_for_review` (or null with a
   named next step), candidates ordered suitable first then by re-evaluated priority, confirmed-incompatible candidates listed
   apart, and the prerequisites Function 06 still needs (owner review, qualification check against verified candidate
   facts, fresh eligibility review, observed form, execution manifest validation, separate authorization). Output carries
   `evidence_origin`: `live_employer_response` (real, `function06_eligible: true`) or `offline_replay_synthetic` (never
   real evidence, `function06_eligible: false`, next step says so).

### Single-use budget, input lineage and attempt evidence (pre-live review fixes)

* **Budget.** `--live` needs `--budget-dir` (a persistent owner-only directory, separate from `--out-dir`). Before any
  request, `reserve_budget` creates, exclusively (`O_CREAT|O_EXCL`, fsync), one file per allowlist digest and one per
  detail URL. If any exists the run is refused with zero requests, regardless of output directory, allowlist or concurrent
  process (an 8-thread test admits exactly one). Nothing releases a reservation: a crash, interruption or uncertain attempt
  stays consumed and is never retried automatically. Only the owner may clear files in the budget directory, after reading
  the attempt journal. Same pattern as Function 02: reservation published durably before the request.
* **Lineage.** `prepare` and `verify` take `--f02-report` and `--expected-f02-sha256`: the exact Function 02 file Function 04
  was run on and the owner's independently recorded digest of it. Function 04 is regenerated from that pinned data with the
  approved criteria and must equal the given report row for row, including `source_report_sha256`. A self-consistent report
  made from other data is refused (`F04_REPORT_NOT_FROM_PINNED_F02_DATA`). The lineage is part of the digest-approved
  allowlist, and the re-evaluation runs over the pinned projection, not over the report's own rows.
* **Attempt evidence.** `attempt-journal.jsonl` (fsync per line, never rewritten): `attempt_started` is written before each
  request, `attempt_finished` right after the response with actual HTTP status, request time, completion time, duration,
  byte count and SHA-256 of the body; the raw body is stored immediately, before analysis. An interruption leaves a started
  record without a finished one (uncertain) and the evidence collected so far.

### Evidence preservation

`raw/<tenant>__<posting_id>.json` (0600, same layout Function 04's `--details-dir` reads), `evidence-manifest.json`
(URL, request and observation timestamps UTC, HTTP status, byte count, SHA-256, stored path, evidence origin), and the report
repeats status, timing, bytes and digest per request plus the carrier log. Everything lives in the owner-only output
directory outside the repository. Nothing private (CV, candidate facts, form data) is read or written.

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
| 15 | Live path gated | Met | CLI tests: no digest, wrong digest, empty digest, no budget dir: exit 2, zero requests; approved digest: only allowlisted URLs, once each |
| 17 | Durable single-use budget, concurrent and cross-directory safe, interruption consumes it | Met | budget tests, interrupted-run test (3 calls, no retry, rerun refused) |
| 18 | Invalid/unreadable/mismatched response stops the employer at once | Met | `test_invalid_or_unbound_response_stops_the_employer_immediately` (6 cases) |
| 19 | Actual status, timing, bytes, digest; evidence kept on interruption | Met | request-metadata, sink-order and journal tests |
| 20 | Never "suitable"; contradictions and compensation are not exclusions; only approved K2 blockers exclude | Met | five tests under review fixes |
| 21 | Lineage bound to pinned original F02 data; synthetic replay labelled and ineligible | Met | lineage, forged-report and replay-label tests |
| 16 | Prioritized, non-executable Function 06 hand-off | Met | three hand-off tests |

Validation (offline): 98 Function 05 tests; focused regressions (Function 04, W2 bridge, discovery, expansion live/replay)
356 passed. Full-suite result is in the PR description.

## Phase 4: bounded live pilot (prepared, not executed)

* Maximum **5** GET requests; exactly the allowlisted `https://api.smartrecruiters.com/v1/companies/<tenant>/postings/<id>`
  endpoints; each at most once; no browser, no application form, no CV, no submission.
* The real shortlist is **not known to this work**: the private store (`.ejs-local`) was not readable in this session and
  nothing was bypassed. Per the baseline, the expected shortlist is the five `preliminary_high` rows of the KPMGNederland/NL
  pilot (`KPMGNederland`, SmartRecruiters, region `global`), so the expected request count is **5** (fewer if fewer rows bind).
  Exact posting ids and canonical URLs appear only after step 1 below.

### Step 1: owner generates the sanitized shortlist (offline, no request)

Set the two values that only the owner has, then run the command. `F02_REPORT` is the exact file that was passed to
`function04_suitability.py --report` (inside `function02-acceptance-20261126`); `F02_SHA` is the SHA-256 the owner recorded
for that file at the Function 02 handoff (not a value computed now from the file).

```sh
F02_REPORT="$HOME/Local/Projeler/EJS/.ejs-local/function02-acceptance-20261126/<file passed to function04 --report>"
F02_SHA="<recorded SHA-256 of that file>"
cd ~/Local/Projeler/EJS/ejs-function-05-detail-verification && PYTHONPATH=src /Users/cagataybuyuk/europe-job-scan/.venv/bin/python \
  scripts/function05_detail_verification.py prepare \
  --f04-report "$HOME/Local/Projeler/EJS/.ejs-local/f04-suitability-20261126-125406/suitability-report.json" \
  --criteria "$HOME/Local/Projeler/EJS/.ejs-local/w1-pilot/criteria.approved.yaml" \
  --expected-criteria-sha256 57faeefb94441abe9f659f8ce636b35c42cbe1b2411e67035ef296d485191f73 \
  --expected-rule-version EJS-W1-PILOT-20261125-v1 \
  --f02-report "$F02_REPORT" --expected-f02-sha256 "$F02_SHA" \
  --out-dir "$HOME/Local/Projeler/EJS/.ejs-local/f05-allowlist-20261126"
```

It verifies the criteria digest and rule version and the F02 pin, regenerates Function 04 from the pinned F02 data and
requires the F04 report to match exactly, writes `detail-allowlist.json` (owner-only) and prints: `expected_request_count`,
`allowlist_sha256`, each target's `f04_priority_rank`, tenant, posting id, canonical URL and detail URL, and any `unbound`
high rows. It makes no network call. A refusal code (`F02_DIGEST_MISMATCH`, `F04_REPORT_NOT_FROM_PINNED_F02_DATA`, ...)
means the pilot must not proceed; report it unchanged. Paste the printed output on Issue #79 for approval.

### Step 2 (only after separate owner approval of that exact output): the bounded read

```sh
cd ~/Local/Projeler/EJS/ejs-function-05-detail-verification && PYTHONPATH=src /Users/cagataybuyuk/europe-job-scan/.venv/bin/python \
  scripts/function05_detail_verification.py verify --live --approve-allowlist-sha256 <allowlist_sha256 the owner approved> \
  --budget-dir "$HOME/Local/Projeler/EJS/.ejs-local/f05-budget" \
  --f04-report <same> --criteria <same> --expected-criteria-sha256 <same> --expected-rule-version <same> \
  --f02-report "$F02_REPORT" --expected-f02-sha256 "$F02_SHA" \
  --allowlist "$HOME/Local/Projeler/EJS/.ejs-local/f05-allowlist-20261126/detail-allowlist.json" \
  --out-dir "$HOME/Local/Projeler/EJS/.ejs-local/f05-detail-20261126"
```

Use one persistent `--budget-dir` for the whole pilot; it is the single-use record, so keep it outside any output directory.
Offline rehearsal with saved bodies: replace `--live --approve-allowlist-sha256 ... --budget-dir ...` with
`--replay-dir <dir of tenant__posting.json>`; its output is labelled `offline_replay_synthetic` and is never real evidence.
The live command refuses without the exact digest or a budget directory, or when the budget is already consumed (exit 2,
zero requests). It prints verdict counts and the first candidate for review; the report, hand-off, attempt journal and raw
evidence stay in the owner-only directory.

### Stop conditions

Any HTTP error, redirect, 429/403, body above 5 MB, or unreadable/unbound response stops that employer (no retry). A consumed
budget is never reset automatically; the owner decides after reading `attempt-journal.jsonl`. Function 06 does not start
from this hand-off.

## Status

Function 05 offline acceptance: complete. Live pilot: ready, **not approved, not executed**. Real shortlist, real verdict
counts and any first candidate for review: not available until the owner runs step 1 (and, after approval, step 2).
Employer-confirmed autonomous applications remain 0.
