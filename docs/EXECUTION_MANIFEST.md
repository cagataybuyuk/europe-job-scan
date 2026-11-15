# EJS-EXECUTION-MANIFEST-1 — prepared application package (GD-006 step 2)

GD-006 (approved by the owner on 2026-11-16, Drive `03_Gelistirme`) splits an
application into two lanes. The daily Claude run discovers a vacancy, checks
eligibility and observes the application form read-only. The local Playwright
engine later fills the form, uploads the approved CV and submits at most once
under SUBMIT-1.0 and the daily rollout cap. This manifest is the only contract
between the two lanes.

`ejs.services.execution_manifest.load_execution_manifest(raw, now=..., expected_sha256=None)`
validates manifest **bytes** that a trusted caller has already read. It opens no
file, browser or network connection, reads no CV and grants no authority by
itself. Every failure raises `PermissionError("EXECUTION_MANIFEST_REVIEW_REQUIRED")`
without echoing supplied values.

## Privacy boundary

A real manifest contains candidate data. It lives only in the owner's private
local queue directory and never in this public repository, an issue or a PR.
Tests use synthetic values only.

## Shape

| Key | Rule |
|---|---|
| `schema_version` | `EJS-EXECUTION-MANIFEST-1` |
| `execution_id`, `candidate_profile_version` | opaque identifiers |
| `prepared_by_run` | daily run id `YYYYMMDD-HHMM` |
| `prepared_at`, `expires_at` | UTC; `expires_at` within 24 h and still in the future |
| `submit_key` | exactly `submit:<opportunity_key>:<requisition_id>:<candidate_profile_version>` |
| `target` | `ats_family`, `company`, `posting_id`, `requisition_id`, `opportunity_key`, `apply_url` |
| `policy` | `version=SUBMIT-1.0`, `rollout_stage` R2 (cap 1) or R3 (cap ≤ 5), integer `daily_cap` |
| `form` | `observed_at` (UTC, ≤ 6 h before preparation), `fingerprint`, `fields[]`, `cv_upload` |
| `cv` | `library_name`, bare `filename` (`*.pdf`, no path), lowercase SHA-256, `size_bytes` ≤ 10 MiB, `application/pdf` |

Unknown or missing keys at any level, duplicate JSON keys, NaN/Infinity, non-UTF-8
and oversize input are rejected.

Only `smartrecruiters` (`jobs.smartrecruiters.com`) is enabled. Another ATS family
is added only together with its engine adapter, fixtures and an approved GD/TEST
record. The apply URL must be HTTPS on the family host, without credentials,
port, query or fragment.

## Form inventory and fingerprint

Each field records `key`, `label`, `control` (`text`, `email`, `tel`, `textarea`,
`select`, `radio`, `checkbox`), `required`, observed `options` (select/radio only)
and a `canonical_field`. `form_fingerprint()` hashes only the structure (fields
sorted by key plus the CV upload control), never the answers, so the engine can
compare its own live observation with the prepared package. A stale or altered
fingerprint rejects the manifest; the engine must stop on any live difference.

## Answers (SUBMIT-1.0)

| Mode | Allowed for | Source |
|---|---|---|
| Verified Fact | any allowed canonical field except privacy, marketing, demographic, free text | `user_fact:<registry key>` |
| Approved Policy | privacy acknowledgement (A4, checkbox true) and salary expectation (A3) only | `policy:SUBMIT-1.0:A3/A4` |
| Approved Neutral Choice | `demographic.*`, value "Prefer not to say" only | `policy:SUBMIT-1.0:A6` |
| Default No | marketing / talent-pool consent only, never true | none or a policy ref |
| Blank Optional | optional fields only, value null | none |
| Evidence Text | `application.motivation_text`, `application.cover_letter`, `application.question.<slug>` | `evidence:<ref>` |

Work authorization and sponsorship (`work.*`) must be Verified Fact (A2). Select
and radio values must be one of the observed options. Required fields can never
be blank. A canonical field may appear only once.

## What this step does not do

No queue directory, engine runner, adapter change, launchd job or Claude task
change is included. Those are GD-006 steps 3–7, each with its own PR and review.
