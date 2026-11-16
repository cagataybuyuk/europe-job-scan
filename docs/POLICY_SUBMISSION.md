# Policy-based submission ledger and authority (GD-006 step 3)

GD-006 (approved 2026-11-16) replaces the per-target human grant of the reviewed
scope with the owner's standing policy: SUBMIT-1.0 plus the rollout stage. On
2026-11-16 the owner removed the daily cap (Auto Submit Policy Registry A9): stage
**R4 has no daily cap**. R2 (1/day) and R3 (5/day) remain available.

## Same canonical ledger, schema v3

`enable_policy_submissions(legacy, now=...)` adds one table, `policy_submissions`, to the
already pinned canonical ledger in a single transaction (v1 or v2 → v3). It never
changes `first_application` (slot 1, the confirmed first canary) or the reviewed
scope tables. An existing v3 ledger is reused. The legacy view validates v3 strictly;
any unknown table, column or invalid row fails closed. Only SHA-256 digests,
timestamps and the row's cap are stored. A v2 ledger migrates only after its reviewed
attempt was dispatched, or its grant expired before dispatch, so the reviewed
slot and this table can never both submit.

## Lifecycle

| Status | Meaning | Next |
|---|---|---|
| Reserved | one dispatch claimed; engine is filling the form | Submitted, Awaiting User, Released |
| Awaiting User | CAPTCHA, MFA or login appeared; the user must solve it | Reserved (resume), Released |
| Released | aborted before any click (drift, blocker, timeout) | may be reserved again later |
| Submitted / Unconfirmed | committed **before** the click | Confirmed |
| Confirmed | exact employer confirmation evidence | — |

- `mark_started` is committed before the engine clicks submit. A started row is
  never released, retried or replaced, even if the outcome is ambiguous.
- A reservation that was never started had no click; it expires after 30 minutes
  (Awaiting User: 12 hours) and is released automatically.
- Only one Reserved row may exist at a time (G7). Awaiting User rows do not block.
- Duplicates are refused:
  - in this table: the same normalized submit key, or the same opportunity +
    requisition (normalized like `submit_identity`, so case and separators do not
    matter) under any profile version;
  - against slot 1, the reviewed attempt and the reviewed grant's pinned target: the
    same submit key, or the same requisition in any common case spelling or without
    separators. Older
    tables store raw-spelling hashes, so the requisition is compared alone; a rare
    cross-employer requisition collision blocks rather than risks a duplicate.
- The cap (R2/R3) counts submissions started on the Europe/Istanbul day plus every
  reservation that may still start (Reserved, Awaiting User), and is checked again
  at `mark_started`; if that recheck refuses, the adapter must `release` the row.
  R4 rows carry no cap.

## CAPTCHA / MFA (A8)

The engine never solves or bypasses a challenge. It pauses with `await_user`,
notifies the owner, and continues with `resume` only after the owner has solved it
in the visible browser.

## Authority

`authorize_manifest(ledger, raw, now=..., switch=RuntimeSwitch(...), expected_sha256=...)`
returns `RESERVED` only when the kill switch is OFF, the local final-submit flag is
ON, the manifest validates (see `EXECUTION_MANIFEST.md`) and the ledger reserves
it. Defaults are safe: kill switch engaged, final submit disabled. Otherwise it
returns `KILL_SWITCH`, `RUNTIME_DISABLED`, `MANIFEST_REJECTED`, `DUPLICATE`,
`CAP_REACHED`, `BUSY` or `LEDGER_ERROR`. The module performs no browser, file or
network I/O; the SmartRecruiters adapter (step 4) consumes it.
