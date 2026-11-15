# Policy-based submission ledger and authority (GD-006 step 3)

GD-006 (approved 2026-11-16) replaces the per-target human grant of the reviewed
scope with the owner's standing policy: SUBMIT-1.0 plus the rollout stage. On
2026-11-16 the owner removed the daily cap (Auto Submit Policy Registry A9): stage
**R4 has no daily cap**. R2 (1/day) and R3 (5/day) remain available.

## Same canonical ledger, schema v3

`enable_policy_submissions(legacy)` adds one table, `policy_submissions`, to the
already pinned canonical ledger in a single transaction (v1 or v2 → v3). It never
changes `first_application` (slot 1, the confirmed first canary) or the reviewed
scope tables. An existing v3 ledger is reused. The legacy view validates v3 strictly;
any unknown table, column or invalid row fails closed. Only SHA-256 digests and
timestamps are stored.

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
- Duplicates: the same normalized submit key, or the same opportunity + requisition
  under any profile version, in slot 1, the reviewed slot or this table, is refused.
- The cap counts dispatched (started) submissions on the Europe/Istanbul day.

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
