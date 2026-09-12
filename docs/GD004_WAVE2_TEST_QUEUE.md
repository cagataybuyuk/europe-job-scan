# GD-004 Wave 2: TEST queue and asset integration

This implements the already approved TEST queue fetch, claim, asset transport and
result reconciliation scope. It does not release employer fill/upload/submit.

## End-to-end contract

The runner receives an explicit row and execution ID, reads an immutable payload,
claims it once, verifies approved synthetic PDFs, performs read-only browser
inspection, records the result and independently reads back its hash/state.

`GD004 TEST Execution Queue` is a dedicated table inside the existing TEST tracker:

| Column | Meaning |
| --- | --- |
| Execution ID | Unique across all staged rows, including completed history |
| Source SHA | Exact SHA embedded by the TEST deployment workflow |
| State | READY, CLAIMED, COMPLETED or REVIEW_REQUIRED |
| Payload JSON | Immutable TEST browser request and approved artifact descriptors |
| Payload Hash | Canonical SHA-256 of the request snapshot |
| Result JSON | Bound, zero-side-effect, sanitized browser result |
| Result Hash | Independently verified canonical SHA-256 of the result |

Rows are initialized only by an owner/editor function. The public Web App has no
administrative or staging operation. Existing business sheets are never modified.
No production queue auto-selection, answer preparation or Applied transition is
introduced by this table. Later live staging and write capabilities remain gated.

Asset approval is part of the immutable payload: artifact ID, exact Drive file ID,
filename, MIME, size, SHA-256 and synthetic=true. The resolver verifies those fields,
PDF signature and direct membership of the approved TEST document folder. It has
read-only Drive scope. The worker validates the same descriptor and bytes in memory;
it neither persists the bytes nor uploads them to an employer.

## Reliability and failure behavior

- One claim winner under Script Lock; duplicate calls do not execute the browser.
- Explicit row + unique execution ID + payload hash fence; source SHA must match
  the deployed code. The queue refuses changed/stale identities.
- Result request/execution/source identities are bound; extra fields, nonzero
  mutation counters, changed hashes and unclaimed results are rejected.
- A persisted result is replayable but cannot be replaced with a different result.
- Interrupted claims stay quarantined for evidence review, not automatic reclaim.
- Browser access/auth/CAPTCHA failures reconcile as REVIEW_REQUIRED, not success.
- Response HMAC is verified before data is used. Signed ok=false is an error, not
  a successful result. Response bytes and Google redirects are bounded.
- HMAC setting is EJS_HMAC_SHARED_SECRET_TEST in both Script Properties and GitHub.

## Verification

Local acceptance uses 22 Node tests executing unchanged Apps Script source in V8
with Google service doubles and 9 Python-to-Apps-Script integration tests. GitHub CI
also runs a real Chromium + Python + Apps Script V8 synthetic roundtrip, alongside
all existing 232 Python tests. The Google service doubles are not a Google deployment.

Live TEST-004B/C remain pending until the owner completes the authorization and
Web App steps in [the bootstrap runbook](GD004_ZERO_COST_APPS_SCRIPT_BOOTSTRAP.md).
Then run `gd004-test-control-plane.yml` on the exact deployed main SHA. Its completion
and independent TEST Sheet readback are the required live acceptance evidence.

## Rollback

Disable the signed queue workflow and remove the TEST Web App deployment. Preserve
queue rows and results as evidence; do not reset claimed jobs. Revert this source
change through a reviewed PR if needed. No PROD rollback is required by this package.

## Safe-fill + CV upload canary and R2 submit gate

TEST-004B/C'nin canlı geçişinden sonraki geliştirme iki ayrı kapıyla uygulanır:

1. `gd004-safe-fill-upload-canary.yml`, `gd004-safe-fill-upload-canary` GitHub Environment incelemesinden sonra çalışır. Canary, bir TEST fixture üzerinde BE-2 güvenli alan yazımı ve FILE-1 tarafından onaylanmış tek PDF CV eklemesini aynı oturumda doğrular. `EJS_FINAL_SUBMIT=false` ve `EJS_KILL_SWITCH=true` zorunludur; kanıt kaydı `submit_attempts=0` içermelidir. `live` modu, hedef manifesti ve Environment incelemesi olmadan fail-closed durumdadır.
2. `gd004-r2-submit-release-gate.yml`, canlı canary kanıtını, exact main SHA'yı ve bağımsız ikinci onayı doğrular. Başarılı çıktı yalnızca `READY_FOR_MANUAL_ENVIRONMENT_REVIEW` manifestidir; runtime submit bayrağını açmaz. Final Submit için ayrıca `gd004-r2-submit-review` Environment reviewer onayı ve ayrı bir yayın değişikliği gerekir.

Bu ayrım, safe-fill/upload kanıtının yanlışlıkla final submit yetkisine dönüşmesini engeller. R2 kapısı; fingerprint kararlılığı, en az bir safe-fill yazımı, tam bir CV upload, insan inceleme kapısına ulaşılması, onaylı asset ve sıfır submit denemesi olmadan açılamaz.
