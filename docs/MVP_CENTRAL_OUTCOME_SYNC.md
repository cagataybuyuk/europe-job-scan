# Confirmed central outcome synchronization (TEST service)

`sync_confirmed_application` is a transport-injected service, disabled for writes
by default. No CLI, existing Apps Script gateway or production Sheet is wired to
this service. There is no built-in network or credential handling.

The trusted caller supplies an actual Confirmed SQLite ledger, independently
pinned source/destination/target binding and a current review. Exact execution,
submit/requisition and acknowledgement evidence digests must match the ledger.
The destination binds one Applications row, all 24 headers and the tracked URL.
The transport must return all matching identities: missing or duplicate matches
stop. A claim that the target row matches is insufficient.

Only J (Status), K (Applied Date) and X (Last Update) can be requested. Dates use
the confirmed timestamp and Europe/Istanbul tracker timezone. Existing later
states, including Rejected, are preserved; an already matching Applied row is a
no-op. Other source cells, formulas, formatting and validation remain untouched.

The write transport must perform a real serialized compare-and-set against the
complete row/schema/validation and identity-match-set fingerprint. It must
recheck identity uniqueness under that same lock; an inserted duplicate prevents
the write. Canonical header order is required even when a changed schema has a
new valid independent pin. A direct Sheets read followed by a
write is not atomic and is not an implementation of that contract. Apps Script
LockService serializes only cooperating script callers, not manual edits or
other connectors. No production race-safety claim is made.

After at most one write request, exact row readback is required. Transport
exceptions after a possible commit remain uncertain and are never blindly
retried. Reconciliation does not submit an application or reopen its budget.

Tests use synthetic rows and real temporary SQLite confirmation records. They
cover one exact three-cell write, restart/idempotence, later-state preservation,
concurrent drift, schema/identity/validation mismatch, unsupported transports,
uncertain commits and sanitized errors. A real locked transport, command wiring,
TEST Sheet integration and independently reviewed live enablement remain open.
