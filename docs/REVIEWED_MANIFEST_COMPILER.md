# W3.6 — offline reviewed manifest compiler (stacked draft)

This increment composes EJS-VERIFIED-FACTS-1 (the existing reviewed input
registry contract), EJS-TRUSTED-FACT-SNAPSHOT-1 (PR #125), and the existing
execution manifest validator. It does not publish or authenticate a real
trusted source and does not change any runtime or submission authority.

## Input and output

- Proposed execution manifest template, with Verified Fact answer objects
  containing the mode, value=null and source_ref=null. Non-fact answers are
  unchanged; the compiler does not independently approve them.
- Independently reviewed registry bytes and externally supplied SHA-256 pin.
  Reuses the existing registry validator, including 30-day freshness,
  verified and auto_fill_approved flags and explicit sources for guarded facts.
- Independently reviewed field snapshot bytes and its separately supplied
  SHA-256 pin, binding profile, CV digest, target, observed form, policy,
  exact controls/options and per-field provenance.
- The trusted caller must get both current unrevoked pins via an independently
  controlled channel, not a manifest writer or adjacent untrusted hash file.
- Output: validated execution manifest bytes in memory. Errors carry only
  MANIFEST_COMPILATION_REVIEW_REQUIRED, with no private JSON error chaining.

Compiler selects only the exact canonical registry key, confirms the exact
type/value and provenance against the reviewed snapshot, then fills placeholders.
No synonym inference, translation, option guessing or implicit bool/string
conversion. Registry fields not accepted by the existing registry validator,
including newer manifest-only employment fields, fail closed until a reviewed
registry contract extension. This is a deliberate compatibility limitation.

The final snapshot validator checks the resulting package, including CV
metadata, target, fingerprint and observed time. No CV bytes are read. The
compiler does not elevate the evidence or approval status of non-fact answers.

## Local trust root: still required

Use a separately reviewed local registry publisher and independent pin
distribution path; verify owner-only access (including ACL and symlink
boundaries), approval/revocation and current profile version before retrieving
bytes. A digest proves equality to the expected bytes, not authority or
revocation. A same-writer pin, stale pin cache, absent pin or unverified source
must not be accepted by a future integration. Revocation and source publication
are not implemented by this pure helper. Do not wire it to the runtime until
the trust root and full package approval have independent review.

## Verification

Synthetic suite: python -m pytest -q tests/test_execution_manifest.py
tests/test_trusted_fact_snapshot.py tests/test_reviewed_manifest_compiler.py
(one command on one line). Existing core CI includes the compiler suite.
No live ATS, private candidate files or actual CVs are involved.

Stacked on PR #125 at 957df28879e3e77016487f7ed4a927e9c2b43efc.
Do not merge before #125 QA is resolved; revalidate against its final interface.
