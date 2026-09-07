# FILE-1 — Approved Document Upload Runtime

FILE-1 adds a separate, bounded browser authority for attaching approved CV and cover-letter PDF assets. It does not weaken BE-2 safe-field authority and does not grant consent, credential, CAPTCHA, protected-field or final-submit authority.

## Core guarantees

- Only `document.cv` and `document.cover_letter` are eligible.
- The asset must be explicitly approved and bound to version, source reference, filename, MIME, size and SHA-256.
- The local asset is verified before browser launch, including PDF magic header.
- The entire upload plan and live file controls are prevalidated before the first attachment.
- Current live form fingerprint must match the inspected fingerprint.
- HTML `accept` constraints are enforced when present.
- Playwright attaches the approved file and reads back browser `File` metadata plus SHA-256 of the browser-side bytes.
- Exact attachment readback must match filename, size, MIME and SHA-256.
- Form fingerprint must remain stable after attachment.
- Verified assets append immutable `SubmissionArtifact` lineage; an exact retry suppresses the duplicate artifact record.
- Raw file bytes and local file paths are not emitted into audit results.
- Final Submit remains disabled.

## Production gate

Current host browser egress remains blocked. FILE-1 production employer upload is therefore disabled until BE-1B can obtain a current trusted external form fingerprint in an outbound-enabled browser runtime. Local canaries may use approved production assets against isolated local fixture forms; these do not create production `Application Execution Log` or `Submission Artifact Registry` rows.
