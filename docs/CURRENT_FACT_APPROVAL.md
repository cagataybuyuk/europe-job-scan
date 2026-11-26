# W3.6 — current approval companion for the existing Fact Registry

This additive offline gate retains the existing EJS-VERIFIED-FACTS-1 registry
and PR #125 snapshot schema; no database, daemon or new dependency is needed.
It is not a trust root, local file reader, publisher, or execution grant.

## Companion record

EJS-CURRENT-FACT-APPROVAL-1 is a strict JSON object with exactly:

- schema_version: EJS-CURRENT-FACT-APPROVAL-1
- generation: positive integer, monotonically advanced by the independent
  publisher whenever the approved pair changes or is revoked
- status: active or revoked (only active passes the consumer)
- profile_version: current approved candidate profile identifier
- policy_version: SUBMIT-1.0
- issued_at, expires_at: UTC seconds, validity at most 24 hours
- registry_sha256, snapshot_sha256: exact reviewed private byte digests

The consumer receives the exact record and private input bytes plus an
independently authenticated CURRENT record digest, generation and profile
version. No defaults or fallback to an earlier approval are permitted.
`resolve_current_fact_inputs` verifies those values, freshness, active status
and both input digests, returning only the pins for the offline compiler.
The compiler and snapshot validator remain responsible for schema, values,
provenance, form, target and CV metadata binding. Do not persist the returned
pins as reusable approval tokens.

## Required operational boundary (not implemented here)

The review authority must be independent of the manifest writer: use the
existing owner-reviewed registry workflow but restrict the publishing identity
and current-state channel so that the writer cannot alter or impersonate them.
Owner-only files under the same OS user, a hash beside a file, a pinned Git
commit in an unprotected repo, or agent-generated signatures with accessible
keys do not establish independence.

The publisher must atomically publish a complete current record, advance
generation on replacement/revocation, and never reissue an earlier generation.
Consumers must obtain the current digest, generation and profile from the
authenticated channel at each decision (not a stale cache); on unavailable
state, missing approval or failed checks, stop. A revoked record and old
record checked against current state fail closed. If an attacker controls
the current-state channel, this helper alone cannot detect rollback.

For a future local adapter, reuse and review the existing pinned private input
reader in single_application_inputs.py: owner-only directory/file, regular
inode, no symlinks/hardlinks, bounded read, inode stability and digest.
That reader is not itself a trust root; additionally review ancestor paths,
effective ACLs, identity separation and atomic publication. Do not read real
candidate files or wire the runtime in this increment. Recheck current
approval at the separately authorized point of use to address revocation
between preparation and use.

## Tests

`python -m pytest -q tests/test_current_fact_approval.py`

Synthetic tests cover exact pin matching, generation rollback, explicit
revocation, expiry, invalid timestamps, malformed JSON and type confusion.
Core CI includes the suite. No live ATS, CV, private data, queue, STOP, ledger
or submission authority changes are in scope.
