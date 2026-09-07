# TEST Cloud Run deployment

Target: `ejs-browser-worker-test` in `europe-west1`.

Authentication: GitHub OIDC -> Google Workload Identity Federation -> dedicated deploy service account. Long-lived service-account keys are prohibited.

Runtime bounds inherited from GD-002: min=0, max=1, concurrency=1, 1 vCPU, 2 GiB, request timeout 180s. External browser capability remains read-only until later approved gates.
