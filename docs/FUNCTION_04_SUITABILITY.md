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
| `tests/test_function04_suitability.py` | 43 acceptance tests, in the core CI job. |

### Evidence levels

| Level | Evidence | May produce |
| --- | --- | --- |
| `listing_metadata` | Title, location, feed country provenance (the Function 02 row) | `preliminary_high / medium / low / blocked`. Always `needs_user` / `vacancy_evidence_missing` (vacancy status unobserved). |
| `vacancy_details` | Identity-bound posting text for that exact `tenant`/`posting_id` | `verified_apply_candidate`, `verified_needs_review`, `verified_blocked`, `verified_below_threshold`. |

Preliminary tiers come from the known dimensions (role track, seniority words, geography). The approved bands are
not used for them: with most components Unknown the best possible listing-only score is below `apply`
(61.5 with the synthetic weights), so every row would land in `skip`. `band` is still reported, with
`band_basis: listing_metadata_unknown_credit_applied`.

### Dimension to evidence mapping

| Dimension | Evidence rule | Score component / signal |
| --- | --- | --- |
| Role and track | Title tokens contain every token of a `role_tracks.primary` / `secondary` phrase. No match is `title only; description not read`. | `role_fit` |
| Seniority | Title matches `seniority.include` / `exclude`. No seniority word is Unknown, not a mismatch. Explicit junior marker (`junior`, `intern`, `trainee`, `graduate`, ...) unless the owner lists it in `include`. | `seniority_title`; blocker `junior-or-technical-only` |
| Geography | Feed-filter country provenance against `countries.tier_1` / `others`. Outside the list or unknown never reaches `auto_apply`; it downgrades to `needs_user`. | No component (none is approved). Review flag, tie-break order |
| Language | Mandatory-language sentence naming a language, minus soft wording, compared to `languages_required_met`. | blocker `mandatory-language-unmet`; `english_intl` when English is explicit |
| Sponsorship | Explicit refusal: blocker plus rating 0. Explicit offer: rating 100. Hedged or absent: Unknown. Both: `contradiction`. | `sponsorship`; blocker `explicit-no-sponsorship` |
| Work authorization | Explicit unrestricted/permanent right or "without sponsorship": blocker unless the candidate's criteria value is `true`. Generic "must have the right to work": `needs_user` flag only. | blocker `unlimited-work-right-required` |
| Compensation | EUR amount with a period. An amount below the net target is confirmed below (net cannot exceed it). Meeting the target is confirmed only when the figure is stated net. Gross or unspecified is `net_comparison_undetermined`. Other currency is Unknown. | `compensation_living` |
| Scope, career upside | Only a reviewer rating with a reference. Otherwise Unknown. | `scope`, `career_upside` |
| Vacancy status | Posting `active` flag. | `closed-vacancy` blocker; Unknown keeps `vacancy_evidence_missing` |

Every row lists `missing_evidence` (what would resolve an Unknown) apart from `confirmed_incompatibilities`
(`confirmed:` from posting text, `title:` from the title alone). Quotes from the posting are kept in `detail_quotes`.

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
* `handoff_ready` is `true` only when the caller passes `criteria_approval_ref` and the criteria have no profile gaps
  (role tracks, seniority, countries, languages, compensation target, work authorization). Without an approval
  reference the output is explicitly unapproved and is not a basis for Function 05.
* Unfilled template placeholders such as `<ROL_AILESI>` are refused (`PROFILE_PLACEHOLDER`).

```sh
PYTHONPATH=src python scripts/function04_suitability.py --report <f02-report.json> --criteria <owner criteria.yaml> \
  --out-dir <absolute owner-only dir outside the repo> [--details-dir <dir of tenant__posting.json>] \
  [--reviewer-ratings <json>] [--criteria-approval-ref <opaque ref>]
```

## Acceptance (Phase 4)

| # | Criterion | Result | Evidence (`tests/test_function04_suitability.py`) |
| --- | --- | --- | --- |
| 1 | Relevant roles prioritized | Met (synthetic criteria) | `test_relevant_roles_rank_first_and_non_matches_last`, geography tier and demotion tests |
| 2 | Explicit hard blockers per approved rules | Met | `test_explicit_blockers_skip_with_quote`, closed vacancy, junior marker, authorized-candidate exception |
| 3 | Unknown sponsorship/compensation not fabricated | Met | `test_unknown_sponsorship_and_compensation_stay_unknown`, hedged, gross, other-currency cases |
| 4 | Missing evidence distinct from confirmed incompatibility | Met | `test_missing_evidence_and_confirmed_incompatibility_are_separate_fields` |
| 5 | Repeated evaluation identical | Met | repeat, shuffled input order, CLI byte-identical re-run |
| 6 | No duplicate identities introduced | Met | duplicate collapse, similar postings kept apart, 198-row uniqueness test |
| 7 | Downstream compatible | Met | Function 02 live-report fixture through projection, suitability and the unchanged W2 bridge |
| 8 | No execution/submission authority | Met | authority block, no `auto_apply` without verified evidence, geography/identity downgrade, network-denied test |

Validation (offline): 43 Function 04 tests pass. Focused regressions (criteria, score/autoreview, dedupe, shadow,
bridge, expansion live/replay/store/integration): 374 passed. Full suite: 3256 passed, 218 skipped, 11 failed, 11 errors.
All 22 failures and errors are Playwright/Chromium launch failures (browser binary not installed in this environment;
files untouched by this change); no browser install was attempted because it needs a download.

## Real data and criteria: blocker

* The owner-only private store `~/Local/Projeler/EJS/.ejs-local/` is deny-listed for this session. The 198-listing
  persistent handoff, its integrity record and the owner's approved criteria were **not read**. Nothing was bypassed.
* Therefore **no real score or decision distribution exists**. Function 02 evidence also differs by one listing
  (pilot recorded 197, the task baseline says 198); this was not reconciled because the bundle is inaccessible.
* Everything above was exercised with synthetic criteria and synthetic listings. The synthetic 198-row run
  (3 countries, 6 titles, listing metadata only) gave 66 high, 33 medium, 66 low, 33 blocked preliminary rows,
  198 `needs_user`, 198 `skip` band, every score component except role and seniority Unknown. This shows the
  mechanism only; it says nothing about the owner's listings.

To produce the real distribution: the owner (or a session with access) runs the CLI against the Function 02 projection
with the approved criteria and `--criteria-approval-ref`, and posts only the counts to Issue #79.

## Remaining owner decisions

1. **Approved criteria file** with filled role tracks, seniority, countries, languages, compensation target and work
   authorization, plus an approval reference. Until then `handoff_ready` is `false`.
2. **Rating percentages** (`SuitabilityPolicy`, `SUITABILITY-1.0-PROVISIONAL`): primary 100, secondary 70, no title
   match 0, seniority include 100 / exclude 0, compensation met 100 / below 0, sponsorship offered 100 / refused 0,
   English met 100 / unmet 0. Only the weights and thresholds are approved today.
3. **Role matching**: token-subset match of the owner's track phrases against the title. Confirm, or supply synonyms.
4. **Junior marker list** and whether `technical-only` can be assessed at all (it stays Unknown: no definition exists).
5. **Geography as a downgrade, not a score component.** No approved component or blocker covers country preference.
   Confirm that outside-list or unknown country should block `auto_apply` (current behaviour) and whether tier 1 should
   influence the score (Sponsor-First Targeting).
6. **Net versus gross**: confirm that no tax model is wanted, or approve one. Without it, gross figures at or above the
   target stay undetermined.
7. **`unknown_sponsorship_is_hard_reject` and `unknown_salary_is_hard_reject`** exist in the template but map to no approved
   blocker, so they are not consumed. Confirm they stay unused (K2 allows Unknown).
8. **Scope and career upside**: who supplies reviewer ratings, and on what reference.
9. **Where posting details come from** for the verified level. This function only reads bytes already collected; collecting
   them is a separate, separately authorized step (W2 `observe_posting_detail` or later).
