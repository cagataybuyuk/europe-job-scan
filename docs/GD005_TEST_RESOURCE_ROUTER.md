# GD-005 TEST advisory router — Issue #62

Scope recorded before integration: user-authorized local TEST implementation,
2026-11-02. Design source: GD-005 v1.0, approved design only, and PR #61.
No runtime integration, deployment, external dispatch, paid API, business-state
write, employer interaction or production approval is included.

`ejs.services.resource_router.route` is pure Python. Callers supply synthetic
opaque task/approval references, exact source SHA, manually declared quota band,
capabilities and provider availability. Approval references are attestations;
the function does not authenticate an approver. Empty permitted actions means
that a successful selection still grants no execution rights. Existing kill
switch, fingerprint, idempotency and readback gates remain independently required.

Normal high-risk design/review may suggest Work then Codex. Other tasks and
all economical/low/exhausted bands prefer existing GitHub CI then an explicitly
available local MacBook. Models never substitute for browser/test execution.
Live browser, interactive authentication and all mutations require human review.
No provider is implicitly available. Network-required tasks need an explicitly
network-capable provider. No cloud or paid provider is accepted.

Manual quota thresholds: 50–100 normal, 20–49 economical, 1–19 low, 0 exhausted.
These are policy bands, not measured account limits. No model/API calls occur.
The sha256 checkpoint covers inputs, source commit, supplied evidence, provider
configuration and decision; it is deterministic and immutable, but not a signed
approval, persisted ledger, or independent proof of a test result. Store only
synthetic metadata; never pass raw content, PII or secrets. A retry with identical
inputs produces identical output. Switching providers changes the checkpoint.

## Local reproduction

```sh
cd /Users/cagataybuyuk/Local/Projeler/EJS/gd005-router
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q tests/unit/test_resource_router.py
.venv/bin/python scripts/ci/secret_scan.py
.venv/bin/python scripts/ci/verify_authority_boundaries.py
```

Router tests need neither Node nor browser installation. The existing CI core
regression job includes the router tests. Remote CI remains unverified until
this branch is published and actual check runs are read back. PR #61 remains
draft; no merge is authorized. Next action: review this isolated implementation.

## Observed local verification — 2026-11-02

Python 3.12 isolated virtual environment; installation of `.[test]` succeeded.
Router suite: 43 passed. Existing CI core-regression file list plus router:
186 passed. Secret scan: PASS. Authority boundary: PASS. Diff whitespace check:
PASS. These are local results, not GitHub check-run results. Browser suites were
not run; this advisory change has no browser/runtime integration. PR #61 was
read back as open/draft at `3be5d079d3a839a323c914637d0ed429939fa48f`;
combined-status connector returned an empty statuses list (no passing CI claim).
Implementation source is based on main `63594d1`; exact implementation commit
is the local TEST branch HEAD. Blocker for remote validation: branch is local,
and GitHub CLI is not authenticated. Next action: review and publish a separate
TEST implementation draft PR, then read actual CI checks for its exact SHA.

## Review correction — 2026-11-02

Review found malformed field types (for example a null task ID, list-valued
risk, or null provider capabilities) could raise before returning human_review.
The router now rejects malformed input schemas before matching or serializing.
Such rejected inputs receive a fixed rejection checkpoint, not a fingerprint of
untrusted arbitrary objects; that reference must not be used as task identity.
Valid requests retain full deterministic checkpoints. No malformed content is
stringified or returned. Added 23 regression cases: router suite now 66 cases;
local core regression 209 passed, secret scan and authority boundary passed.

## Current checkpoint: merged router and proposed synthetic CLI

PR #63 merged as `00c3730332e18f91037b1145fc87cbfccf0f8000`; all six
post-merge checks passed. Earlier local-only/authentication blockers above are
historical evidence, not the current status. Design PR #61 remains draft.

The follow-up `test/gd005-router-smoke` branch adds only a synthetic TEST CLI.
This is an advisory smoke harness, not a browser/control-plane executor. It
has no deployment, network, dispatch, write or upload entrypoint. Provider
availability is a fixture scenario, never a measured operational fact. The
approval reference is caller-supplied metadata, not authenticated authority.
The supplied SHA is validated by the router but not discovered by this CLI.
`test_evidence=not_run` and `provider_executed=false` explicitly prevent the
selection output from claiming a real provider test passed.

From a checkout with `.[test]` installed:

```sh
python -m ejs.apps.resource_router_smoke \
  --environment TEST --approval-ref GD005:TEST:62 \
  --source-sha "$(git rev-parse HEAD)" --remaining-percent 0 \
  --scenario local_only
```

Scenarios: `ci_available`, `local_only`, `no_provider` (default). No provider
returns exit 2 / human_review; a valid suggestion returns exit 0, which means
only advisory selection succeeded. Unsupported scope/options also exit 2.
No PROD environment, execute option, paid API or final-submit option exists.
Use synthetic metadata only. The existing core CI job runs CLI process tests.
This follow-up requires separate review and merge approval; deployment and
actual runtime integration remain out of scope.
