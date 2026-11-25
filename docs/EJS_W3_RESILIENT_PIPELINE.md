# W3.1 — Resilient application preparation (inspection only)

- Uses the W2 discovery-to-application bridge, current live inspector and a read-only view of the canonical submission ledger. No queue or submission authority is granted.
- The private retry state is checkpointed atomically before and after each attempt; later runs respect exponential backoff, Retry-After, and access holds. Failed form observations do not permanently reserve a duplicate requisition; fresh form observations expire after the manifest observation window. A confirmed or uncertain submission in canonical history is never eligible for another observation/automatic submission through this path.
- Browser inspection denies unknown POSTs, unreviewed redirects, untrusted navigation and external resources. Challenges remain access/review holds, never bypassed. A lack of form fields is not proof of a bot challenge. An optional external resource denial alone is diagnostic, not permission to fetch it.
- Success means structural evidence for a separate manifest review, not candidate eligibility, a completed manifest, form filling or a submitted application. The report retains the source-detail digest and observation schema fingerprint; the latter is NOT the execution-manifest fingerprint. Form options, candidate facts, CV and eligibility evidence still require binding and review.
- CLI requires explicit `--live-read-only`, a per-pilot budget of 1–5, owner-only private input/output paths, and `--exclusions` (an explicit JSON list, which may be empty). The exclusions file is a one-time input, not a second application ledger. State and report remain outside Git. The pilot budget is not a production daily cap. Do not run a live pilot without separate target/scope authorization.
- Offline test command: `python -m pytest -q tests/test_application_batch.py tests/test_application_bridge.py tests/test_policy_submission_ledger.py tests/test_execution_manifest.py tests/unit/test_smartrecruiters_live_inspector.py tests/unit/test_smartrecruiters_live_route.py tests/test_mvp01_render_structure_wait.py tests/test_mvp02_network_evidence.py tests/test_mvp03_observation_boundaries.py tests/test_mvp03_frame_observation_guard.py tests/test_mvp03_dynamic_render_signals.py tests/test_mvp03_human_handoff_contract.py tests/test_mvp03_captcha_precedence.py`.

## W3.2.1 redirect diagnostics

Sanitized redirect evidence is retained in the inspection report and
batch state. Every redirect remains denied. Recovery and network policy
changes are deferred pending separately authorized live evidence.
