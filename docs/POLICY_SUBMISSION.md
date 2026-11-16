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
- A Reserved row that was never started had no click; it expires after 30 minutes and
  is released automatically. Awaiting User rows never expire on their own: the owner
  may have submitted by hand, so only `release(..., owner_confirmed_no_submit=True)`
  frees them. `resume` is possible within 12 hours. After a handoff the runtime moves
  its own unstarted row to Awaiting User atomically (`hold_for_owner`, no TTL), also
  taking back a row of its own that expired meanwhile. A row that ever went through a
  handoff carries `owner_handoff=1`: it never expires to Released (it returns to
  Awaiting User) and is released only with the owner's confirmation.
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

## Runtime (GD-006 step 4a)

`PolicyApplicationRuntime(browser, ledger).run(raw, expected_sha256=..., cv_path=..., switch=...)`
drives one manifest through a `PolicyFormBrowser` (open, structure-only inspect,
write/readback, upload, owner wait, submit once):

1. `authorize_manifest`; anything but `RESERVED` returns before the browser opens.
2. CV bytes are read owner-only (dir 0700, file 0600, no symlink) and must match the
   manifest's SHA-256, size and file name.
3. The live form fingerprint must equal the manifest's; every non-blank answer is
   written and read back; Blank Optional fields are never touched. The runtime clock
   never moves backwards, and the ledger clamps release times to the reservation.
4. The approved CV is uploaded and verified, then every answer is read back again and
   the attachment (hash, size, name, visible) is checked on a fresh inspection.
5. A CAPTCHA/MFA/login challenge moves the row to Awaiting User; the browser waits for
   the owner (default up to 6 h) and the runtime verifies the surface again. It never
   solves a challenge itself.
6. `mark_started`, then exactly one `submit_once`; only an acknowledged and visible
   confirmation becomes `EMPLOYER_CONFIRMED` via `ledger.confirm`.

Every failure before step 6 releases the reservation (`BLOCKED`, no click), except
after any owner handoff: the owner may have acted in the page, so the row is held as
Awaiting User for the owner to resolve. After
`mark_started` the result is `SUBMITTED_UNCONFIRMED` unless confirmed, and the key is
consumed forever. Results carry fixed `POLICY_APP_*` codes only.

Multi-page forms (GD-006 A1): each page's live structure is compared with that
page's manifest fields; answers are written and read back per page; the CV is
checked on its own page before Next. In an observe pass the runtime presses Next
after the last known page, returns `OBSERVED` with that page's structure only, and
releases the reservation (held for the owner after a handoff). It never clicks submit. The SmartRecruiters
Playwright binding (multi-step page, network boundary) is step 4b.
