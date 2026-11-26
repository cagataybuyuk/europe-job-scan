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

## Private local sources

`compile_current_manifest_from_files` accepts four absolute file paths and the
same independently authenticated current approval context as the byte API. Keep
private inputs outside the repository on a trusted local filesystem. Each input
needs an owner-only `0700` parent and a caller-owned `0600` regular file with one
link. The reader opens each component relative to a verified directory
descriptor without following symlinks. Ancestors must belong to the caller or
root. Group or other write access is permitted only on root-owned sticky
ancestors such as `/tmp`
on Linux or `/private/tmp` on macOS. Pass physical paths when the OS also
exposes a symlink alias.

On macOS the reader checks descriptor-based extended ACLs and accepts only
absent or deny-only ACLs. On Linux it rejects extended POSIX access ACLs,
including masked grants. Failed ACL inspection and unsupported platforms deny
the read. The reader checks the path bindings and file metadata again after all
four reads. It caps each read at one byte beyond the inspected file size and
uses the existing per-input limits of 16,384 bytes for approval, 512,000 bytes
for template, 1,000,000 bytes for registry and 500,000 bytes for snapshot.
The existing gate compares the approval bytes against the independent digest
and uses the authenticated record's pins for the other three byte sequences.
Errors expose only `CURRENT_MANIFEST_REVIEW_REQUIRED` from the file entry point.

Run offline:
`python -m pytest -q tests/test_execution_manifest.py tests/test_trusted_fact_snapshot.py tests/test_reviewed_manifest_compiler.py tests/test_current_fact_approval.py tests/test_current_manifest_compiler.py tests/test_current_manifest_local_sources.py`

## Operational decision still required

Prefer the existing owner-reviewed registry rather than a new database or
service. An independent review identity must publish authenticated CURRENT
approval state, protected from the manifest writer (including rollback).
A file owned by the same user or a hash computed by the writer is not enough.
A separately permissioned owner-controlled approval channel is a candidate,
and its actual identities, permissions, publication, current-state retrieval
and point-of-use revocation recheck must be verified before integration.
Do not assume a signing key or same-UID storage provides that separation.
The current helper tests a supplied trust context; it cannot authenticate or
refresh that context. Compiled bytes do not confer execution authority.
No actual candidate data, live ATS, runtime, STOP, ledger or policy change.
