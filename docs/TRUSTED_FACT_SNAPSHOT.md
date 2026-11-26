# W3.6 — offline trusted fact binding (review draft)

This increment is an additive validator, not an executor. It does not modify
the queue, runtime, STOP, ledger, policy, adapters or submission authority.

`verify_fact_snapshot(manifest_raw, snapshot_raw, expected_snapshot_sha256=..., now=...)`
reuses the existing execution manifest loader. The caller must obtain snapshot
bytes and the expected SHA-256 from an independently trusted local review source,
not from the manifest, the manifest writer, or an untrusted adjacent hash file.
The validator does not authenticate the reviewer or establish that trust root.
Do not connect it to a live execution path until that independent source and
its ownership, access, expiry and revocation rules have been reviewed.

## Snapshot shape (EJS-TRUSTED-FACT-SNAPSHOT-1)

Strict JSON object; unknown or duplicate keys rejected; max 500,000 bytes.
Private local material only; never commit actual candidate data or digests.

- schema_version: EJS-TRUSTED-FACT-SNAPSHOT-1
- profile_version, cv_sha256: exact independently reviewed manifest binding
- policy: SUBMIT-1.0
- issued_at, expires_at: UTC seconds, issued no earlier than the observed form,
  not in the future, expires strictly after validation, validity at most 24 h
- target: exact ats_family, company, posting_id, requisition_id,
  opportunity_key and apply_url
- form_fingerprint: exact existing structural fingerprint
- facts: exactly one reviewed row per Verified Fact field; no missing,
  duplicate or extra rows. Each row contains key, canonical_field, fact_key,
  step, control, options (ordered list or null), value (exact type and value)
  and nonempty provenance (opaque independently reviewed source reference).

The trusted source must review the fact-to-field mapping and option identity
against the observed form independently, not copy proposed manifest answers.
The validator compares the reviewed data; it does not infer or translate values.
Failure produces only FACT_SNAPSHOT_REVIEW_REQUIRED. A successful return is
not authority to fill, upload, submit, or to approve Evidence Text or other
answer modes. Actual CV bytes are not read in this increment.

## Tests and remaining release gates

Synthetic regression tests cover altered answers with unchanged fingerprint,
option drift, context, expiry, source pin mismatch and malformed input.
Run offline: `python -m pytest -q tests/test_execution_manifest.py tests/test_trusted_fact_snapshot.py`.

Still open: trusted local registry publication and pin distribution, snapshot
compiler from independently reviewed facts, independent QA, evidence/policy
review as applicable, and separately reviewed integration before any writes.
No live request is part of this increment.
