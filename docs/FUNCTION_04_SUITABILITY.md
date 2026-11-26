# FUNCTION-04: Candidate–Listing Suitability

Base: Function 02 PR #133 head `04ccca6` (stacked). Tracking: Issue #79. Scope: function 04 only. Nothing here
touches the network, a browser, an application form, the ledger, `engine-queue/control/` or any Function 02 file.
Output carries `executable: false`, `submit_authorized: false` and an `authority` block with every power `false`.
A decision label (`auto_apply`, `auto_skip`, `needs_user`) is a label, never authority.

## Existing capability (Phase 1) and what was missing

| Component | State | Decision |
| --- | --- | --- |
| `criteria_loader` | Validates weights (sum 100), thresholds, five mandatory hard blockers. Keeps preference sections only in `Criteria.raw`. | Reused unchanged. `profile_from_criteria` reads `raw`. |
| `opportunity_score` (OPPORTUNITY-1.0) | Exact `Fraction` score, bands 85/75/65, Unknown earns `unknown_credit_percent`. Needs ratings. | Reused unchanged. |
| `autoreview` (K2) | Label from facts: closed vacancy and Unknown status fail closed, blockers skip, Unknown never blocks. Needs tri-state facts. | Reused unchanged. |
| `shadow_run` | Rates every component Unknown for every listing. | Left as is. It is the discovery step; Function 04 supplies the evidence it lacks. |
| `application_bridge` (W2) | Two narrow regexes over posting text, readiness status. No scoring, no role, geography, language or compensation. | Left unchanged. Function 04 output is projected into its input contract. |
| **Missing** | Nothing turned a listing or posting text into ratings and blocker signals. No role/seniority/geography/language/compensation assessment, no distinction between listing-metadata priority and verified suitability, no missing-evidence output, no ordered hand-off. | Implemented (below). |

The scoring engine was not replaced: no demonstrated need.

## What was added

| File | Purpose |
| --- | --- |
| `src/ejs/services/suitability_evidence.py` | Offline evidence extraction from already-collected posting bytes/text. Explicit statements only. Hedged ("may", "depending", "exceptional candidates") or soft ("is a plus") wording stays Unknown. Identity-bound SmartRecruiters adapter (`tenant` + `posting_id` must match). EUR compensation only, with an explicit period; no FX, no tax model. |
| `src/ejs/services/suitability.py` | `evaluate_report` (deterministic prioritization), `project_for_bridge` (priority-ordered view for the unchanged W2 bridge), `profile_from_criteria`. |
| `scripts/function04_suitability.py` | Offline CLI. Owner-only output (0700 dir, 0600 files) outside the repo, no overwrite, prints counts only. |
| `tests/test_function04_suitability.py` | 76 acceptance tests, in the core CI job. |

### Evidence levels

| Level | Evidence | May produce |
| --- | --- | --- |
| `listing_metadata` | Title, location, feed country provenance (the Function 02 row) | `preliminary_high / medium / low` only. Always `needs_user` / `vacancy_evidence_missing`. Never an exclusion. |
| `vacancy_details` | Identity-bound posting text for that exact `tenant`/`posting_id` (collected by Function 05, supplied here as bytes) | `verified_apply_candidate`, `verified_needs_review`, `verified_incomplete`, `verified_below_threshold`, `verified_blocked`. |

Owner decisions of 26 Nov 2026 applied: primary title match 100, secondary 70, **no title match = Unknown rating and
`preliminary_low`, not an incompatibility**. The preliminary tier comes from the role-track title match alone;
geography is a tie-break (after the exact score) and a review gate, seniority words only move the 3 % component; neither
changes a tier. The approved bands are not used for preliminary rows: with most components Unknown the best listing-only
score is below `apply` (61.5 with the synthetic weights). `band` is still reported with
`band_basis: listing_metadata_unknown_credit_applied`.

Confirmed versus incomplete:

* `confirmed_incompatibilities` only holds what the employer's posting states (`confirmed:blocker:...`,
  `confirmed:vacancy_closed`, `confirmed:compensation_below_target`).
* `preliminary_signals` holds title-only observations (`title_no_role_track_match`, `title_seniority_excluded_term`,
  `title_junior_marker:<word>`). They lower nothing beyond the 3 % seniority component and exclude nothing.
* A verified row below the auto-apply minimum is `verified_below_threshold` only if even rating every Unknown component
  100 cannot reach the minimum. Otherwise it is `verified_incomplete` (reason `score_below_min_evidence_incomplete`) and
  stays in the hand-off. The engine's `auto_skip` label is kept as the approved engine output; it is not an exclusion.
* The hand-off drops only `verified_blocked` and `verified_below_threshold`.

### Dimension to evidence mapping

| Dimension | Evidence rule | Score component / signal |
| --- | --- | --- |
| Role and track | Title tokens contain every token of a `role_tracks.primary` (100) / `secondary` (70) phrase. No match: rating Unknown, low priority, review flag before any apply label. | `role_fit` |
| Seniority | Title matches `seniority.include` / `exclude`. No seniority word is Unknown. Junior is a blocker only with **both** a title marker (`junior`, `intern`, `trainee`, `graduate`, ...; skipped if the owner lists it in `include`) **and** a posting statement (`entry-level`, `0-2 years`, `internship`, ...). One source alone is a `junior_signal_needs_review` flag. | `seniority_title`; blocker `junior-or-technical-only` |
| Geography | Feed-filter country provenance against `countries.tier_1` / `others`. Tie-break after the exact score. Outside the list or unknown never reaches `auto_apply`; it downgrades to `needs_user`. | No component. Review gate and tie-break only |
| Language | Mandatory-language sentence naming one language, minus soft wording, negation, conditions and "X or Y" alternatives (those are ambiguous), compared to `languages_required_met`. | blocker `mandatory-language-unmet`; `english_intl` when English is explicit |
| Sponsorship | Explicit refusal: blocker plus rating 0. Explicit offer: rating 100. Hedged, conditional, scoped (`unless`, `except`, `currently`, `generally`, `may`, ...) or absent: Unknown. Both: `contradiction`. Unknown sponsorship is never a hard reject. | `sponsorship`; blocker `explicit-no-sponsorship` |
| Work authorization | Requirement cue (`must`, `need`, `required`) plus unrestricted/permanent right or "without sponsorship", and no negation ("not required"), preference or condition. Blocker only if the criteria say the candidate is **not** authorized; criteria value unknown stays Unknown (`work_authorization_unknown_for_requirement`). Generic "must have the right to work": `needs_user` flag only. | blocker `unlimited-work-right-required` |
| Compensation | EUR amount with a period. An amount below the net target is confirmed below (net cannot exceed it). Meeting the target is confirmed only when the figure is stated net. Gross or unspecified is `net_comparison_undetermined`. Other currency is Unknown. | `compensation_living` |
| Scope, career upside | Only a reviewer rating with a reference. Otherwise Unknown. | `scope`, `career_upside` |
| Vacancy status | Posting `active` flag. | `closed-vacancy` blocker; Unknown keeps `vacancy_evidence_missing` |

Every row lists `missing_evidence` (what would resolve an Unknown) apart from `confirmed_incompatibilities`
(posting text only) and `preliminary_signals` (title only). Quotes from the posting are kept in `detail_quotes`.

## Hand-off (Phase 3)

* Input: the Function 02 verified projection, unchanged. The CLI projects a live expansion report with the
  existing `project_for_application_bridge`. Identity is `(ats_kind, region, tenant, posting_id)`; provenance fields and
  `discovery_evidence` are copied verbatim. Exact duplicates collapse (counted), rows sharing only a `similarity_key` stay
  separate, are flagged `duplicate_similarity_group` and cannot reach `auto_apply`. A row without `posting_id` is kept as
  `identity_status: unknown` and cannot reach `auto_apply`.
* Output `EJS-SUITABILITY-1`: rows ordered by classification stage, then exact score, then geography tier, then
  identity, with `priority_rank`. No timestamps: same inputs give byte-identical JSON, regardless of input order.
  `source_report_sha256` ties the output to its input.
* `project_for_bridge` drops confirmed-blocked and below-threshold rows (counted) and emits a report the unchanged W2
  `prepare_application_report` accepts.
* **Approval is bound to a digest, not to a reference.** The criteria count as owner-approved only when the SHA-256
  of the exact bytes read equals an independently recorded expected digest **and** the file's rule version equals the
  expected one (`criteria.approval: approved_digest_and_rule_version_match`). Other statuses: `not_verified_no_expected_digest`,
  `digest_mismatch`, `rule_version_mismatch`, `expected_digest_malformed`, `not_verified_no_actual_digest`. The CLI reads the criteria file once, hashes those bytes and
  parses the same bytes (`parse_criteria_text`); a digest or rule-version mismatch, or one expected value without the
  other, stops it before parsing, evaluation or any output directory is created. There is no free-form approval reference any more.
* `handoff_ready` is `true` only for an approved digest and configured role tracks. Other profile gaps stay Unknown and are
  listed in `criteria.profile_gaps`; nothing is invented to fill them.
* The output separates `policy.owner_approved_fields` (primary 100, secondary 70, no title match) from
  `policy.provisional_fields` (all other rating percentages).
* Unfilled template placeholders such as `<ROL_AILESI>` are refused (`PROFILE_PLACEHOLDER`).

```sh
PYTHONPATH=src python scripts/function04_suitability.py --report <f02-report.json> --criteria <owner criteria.yaml> \
  --out-dir <absolute owner-only dir outside the repo> [--details-dir <dir of tenant__posting.json>] \
  --expected-criteria-sha256 <recorded digest> --expected-rule-version <recorded rule version> [--reviewer-ratings <json>]
```

## Acceptance (Phase 4)

| # | Criterion | Result | Evidence (`tests/test_function04_suitability.py`) |
| --- | --- | --- | --- |
| 1 | Relevant roles prioritized | Met (synthetic criteria; real criteria blocked) | `test_relevant_roles_rank_first_and_non_matches_last`, geography tier and demotion tests |
| 2 | Explicit hard blockers per approved rules | Met | `test_explicit_blockers_skip_with_quote`, closed vacancy, junior marker, authorized-candidate exception |
| 3 | Unknown sponsorship/compensation not fabricated | Met | `test_unknown_sponsorship_and_compensation_stay_unknown`, hedged, gross, other-currency cases |
| 4 | Missing evidence distinct from confirmed incompatibility | Met | `test_missing_evidence_and_confirmed_incompatibility_are_separate_fields` |
| 5 | Repeated evaluation identical | Met | repeat, shuffled input order, CLI byte-identical re-run |
| 6 | No duplicate identities introduced | Met | duplicate collapse, similar postings kept apart, 198-row uniqueness test |
| 7 | Downstream compatible | Met | Function 02 live-report fixture through projection, suitability and the unchanged W2 bridge |
| 8 | No execution/submission authority | Met | authority block, no `auto_apply` without verified evidence, geography/identity downgrade, network-denied test |

Validation (offline): 76 Function 04 tests pass (33 added for the review gaps: digest binding, negative/conditional/ambiguous
employer statements, unknown candidate work authorization, title-only signals, incomplete versus confirmed). Focused
regressions (criteria, score/autoreview, dedupe, shadow, bridge, expansion live/replay/store/integration): 402 passed.

## Real 198 listings: status

Function 02 reconciliation (owner, 26 Nov 2026): the earlier pilot held 197 listings, the final acceptance 198 (one
newly added posting).

**Blocked, not bypassed.** Reading `~/Local/Projeler/EJS/.ejs-local/w1-pilot/criteria.approved.yaml` (digest check) was
denied by the harness permission classifier in this session, as was the directory earlier. The approved bytes, the digest
verification and the 198-listing handoff were therefore not read, and **no real distribution exists in this PR**. No other
route was tried. Synthetic fixtures are not substituted for the owner's criteria and no candidate fact is reconstructed.

Minimal owner action: run the single command below in a session or terminal that can read the private store. It verifies the
digest, refuses a different file, evaluates the 198 rows offline (no network, no form, no submission), writes the report to an
owner-only directory and prints counts only. Paste that output on Issue #79.

```sh
cd ~/Local/Projeler/EJS/ejs-function-04-suitability && PYTHONPATH=src /Users/cagataybuyuk/europe-job-scan/.venv/bin/python \
  scripts/function04_suitability.py \
  --report "$HOME/Local/Projeler/EJS/.ejs-local/f02-acceptance-<YYYYMMDD>/report.json" \
  --criteria "$HOME/Local/Projeler/EJS/.ejs-local/w1-pilot/criteria.approved.yaml" \
  --expected-criteria-sha256 57faeefb94441abe9f659f8ce636b35c42cbe1b2411e67035ef296d485191f73 \
  --expected-rule-version EJS-W1-PILOT-20261125-v1 \
  --out-dir "$HOME/Local/Projeler/EJS/.ejs-local/f04-suitability-<YYYYMMDD>"
```

Output keys: `listings_kept`, `duplicate_rows`, `classifications` (the preliminary priority distribution),
`missing_evidence`, `review_flags`, `preliminary_signals`, `exclusions`, `downstream_candidates`, `criteria_approval`,
`profile_gaps`. With listing metadata only, expect `exclusions.count` 0 (nothing is confirmed from a title) and
`downstream_candidates` equal to `listings_kept`. A `criteria_approval` other than
`approved_digest_and_rule_version_match`, or a nonzero exit, means the run is not a valid acceptance; report it unchanged.
The 198-row `report.json` produced by Function 02 acceptance is accepted directly: the CLI applies the existing
`project_for_application_bridge` first. If the real criteria file uses a shape this loader rejects, the CLI prints the
refusal code and writes nothing.

## Remaining owner decisions

1. Provisional rating percentages other than the three approved ones (seniority include/exclude, compensation, sponsorship,
   English), listed in `policy.provisional_fields`.
2. Junior marker words and posting phrases, and whether `technical-only` can ever be assessed (it stays Unknown).
3. Token-subset matching of role-track phrases against titles: confirm, or supply synonyms.
4. Whether tier-1 countries should ever influence the score (today: tie-break and review gate only).
5. `unknown_sponsorship_is_hard_reject` and `unknown_salary_is_hard_reject` are not consumed (decided: Unknown is not a hard reject).
6. Who supplies scope and career-upside ratings, and on what reference (Unknown without evidence).
7. Function 05 owns vacancy detail collection; this function only reads bytes it is given.
