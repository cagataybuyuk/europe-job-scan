# W3.6 — Integrated offline approval and compilation

## Pinned components

- #125 validator, ancestor 957df28879e3e77016487f7ed4a927e9c2b43efc
- #127 compiler, branch base 0174ef87e38837720bc3873d4013969e6e921f6a
- #128 current approval, imported exact three file blobs from
  373d14c2f9f81315cebeb8cc66dbe5cf40d5b086

The #128 CI test entry is combined with the existing compiler and validator
entries. This review branch is not a merge of the source PRs into main;
all source branches remain unchanged. Reconcile against their final heads
before any release.

## Offline composition

`compile_current_manifest` accepts four exact byte inputs and the independently
obtained current approval digest, generation, profile and current UTC time.
It resolves current approval, uses the returned three pins in the compiler,
reuses manifest validation and checks the compiled profile against the
independent current profile. No I/O, browser, queue or execution authority.
A fixed denial code is returned on failure through PermissionError.

The integration tests exercise valid deterministic compilation, byte changes
to each input, mismatched current profile, revocation/rollback, expiry,
downstream denials even with matching pins and private error-frame cleanup.

Run offline:
`python -m pytest -q tests/test_execution_manifest.py tests/test_trusted_fact_snapshot.py tests/test_reviewed_manifest_compiler.py tests/test_current_fact_approval.py tests/test_current_manifest_compiler.py`

## Operational decision still required

Prefer the existing owner-reviewed registry rather than a new database or
service. An independent review identity must publish authenticated CURRENT
approval state, protected from the manifest writer (including rollback).
A file owned by the same user or a hash computed by the writer is not enough.
A separately permissioned owner-controlled approval channel is a candidate,
but its actual identities, permissions, revocation retrieval, private file
reader and point-of-use recheck must be verified before integration.
Do not assume a signing key or same-UID storage provides that separation.
The current helper tests a supplied trust context; it cannot authenticate or
refresh that context. Compiled bytes do not confer execution authority.
No actual candidate data, live ATS, runtime, STOP, ledger or policy change.
