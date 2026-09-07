# Europe Job Scan — Cloud Run Browser Worker TEST v1

GD-002 execution-plane bootstrap. The service exposes authenticated `/health` and `/v1/execute` endpoints. GD-002 operations are restricted to `echo`, packaged `inspect_fixture`, and external `inspect_read_only`.

Hard invariants: TEST only; form write/upload/credentials/consent/CAPTCHA bypass/final submit are rejected; external targets must be public HTTPS; private/link-local/metadata/local targets are blocked; caller-controlled file/local schemes are not accepted.

Deploy from the repository root with `google_native/cloud_run/deploy/bootstrap_test.sh` after a TEST Google Cloud project and billing account are ready.
