# MW-3 Append-Only Canary

## Status

MW-3A infrastructure canary implemented on 2026-08-17.

## Released authority

Initial production canary authority is restricted to the dedicated `service_canary_audit` domain. Production business-state writes and external application-form writes remain disabled.

The operational append-only domains below are implemented in the authority model but are feature-flagged OFF until a real event is available:

- Source Health Log
- Discovery Funnel Log
- Outcome Event Log

This prevents synthetic test data from contaminating operational source/funnel/outcome history.

## Idempotency

- Source run key contract: `run:<run_id>:<source_key>` per IDEMPOTENCY-STATE-1.0 / ID-13.
- Infrastructure canary key: `canary:MW-3:<run_id>:<domain>`.
- Exact retry is a no-op and is reported as `duplicate_suppressed`.

## Canary result

Run: `MW3-CANARY-20260817-1134`

- First append: pass
- Retry duplicate suppression: pass
- Reconciliation gap: 0
- Business-state mutations: 0
- External-form mutations: 0
- Unit/safety suite: 23/23 pass

## Next gate — MW-3B

On the next real Europe Job Scan execution, release exactly one actual append-only operational domain behind a feature flag (recommended: Source Health Log + its paired Discovery Funnel measurement for one bounded source). Pre-read the deterministic key, append once, re-read, verify exact-one row and zero reconciliation gap, then expand only after the canary remains green.
