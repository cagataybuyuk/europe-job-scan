# W3.6 — offline reviewed manifest compiler (stacked draft)

This increment composes EJS-VERIFIED-FACTS-1 (the existing reviewed input
registry contract), EJS-TRUSTED-FACT-SNAPSHOT-1 (PR #125), and the existing
execution manifest validator. It does not publish or authenticate a real
trusted source and does not change any runtime or submission authority.

## Input and output

- Independently reviewed execution manifest template and its exact-byte SHA-256
  pin (`expected_template_sha256`). Verified Fact answer objects contain the
  mode, `value=null` and `source_ref=null`. The pin binds every other part of the
  template, including non-fact answers, CV metadata, rollout stage, pass and
  execution lifetime. Review of those values belongs to the caller's reviewer.
- Independently reviewed registry bytes and externally supplied exact-byte SHA-256
  pin (`expected_registry_sha256`).
  Reuses the existing registry validator, including 30-day freshness,
  verified and auto_fill_approved flags and explicit sources for guarded facts.
- Independently reviewed field snapshot bytes and the corresponding exact-byte
  SHA-256 pin (`expected_snapshot_sha256`). The snapshot binds the profile
  version, CV digest, target, observation instant, policy version, form
  fingerprint and reviewed fact rows.
- Output is validated execution manifest bytes in memory. Failures raise
  `MANIFEST_COMPILATION_REVIEW_REQUIRED` without the parser exception chain or
  input data in compiler traceback frame locals. Callers own redaction in
  their own frames and logging.

The compiler selects the exact canonical registry key and confirms the exact
type, value and provenance against the reviewed snapshot before filling
placeholders. Fields that cite one fact key, such as email and confirmation,
must agree on value and type. A canonical alias still needs its own exact
registry entry. Manifest-only employment fields require a reviewed registry
contract extension before they can compile.

The final snapshot validator checks the resulting manifest against the fact
review. It compares ordered manifest option strings. A future adapter that needs
separate DOM option values, widget identity or disambiguation of repeated labels
requires a reviewed contract extension. Verification of the CV file's actual
bytes and the content review of evidence and policy decisions belong to the
caller. Compilation evaluates validity at the supplied UTC time. A later
consumer must revalidate the active approval and all applicable expiry windows
before use. The manifest expiry alone cannot express review or registry expiry.

## Local trust root: still required

The future integration needs one authenticated current approval for the three
pins together, issued outside the manifest writer's control. It must bind the
template's non-fact and metadata review to the registry, snapshot, profile and
target that were reviewed together. The approval source must enforce expiry,
revocation and rollback protection for the tuple, including on later use of a
compiled manifest. Private storage must enforce owner-only access, including ACL
and symlink boundaries. A caller-computed digest of proposed bytes only records
their identity. Runtime integration requires independent review of this approval
and publication interface first.

## Verification

```sh
python -m pytest -q tests/test_reviewed_manifest_compiler.py tests/test_trusted_fact_snapshot.py tests/test_execution_manifest.py tests/test_single_application_inputs.py
```

Core CI includes all four synthetic suites. The compiler composes the PR #125
interface at `957df28879e3e77016487f7ed4a927e9c2b43efc`, including its exact
observation binding. Revalidate against the dependency's final head before merge.
