# MW-4C — Derived Authority Expansion + Full User Action Projection Shadow

Run: `MW4C-20260817-1607`

Goals:
1. Confirm MW-4B `Form Fill Queue!S:X` ownership is exhaustive for the currently eligible High-confidence active readiness scope.
2. Reproduce the complete `User Action Center` materialized queue from its authoritative source surfaces without writing to `User Action Center`.

Projection sources and semantics mirror the current Workspace formula contract:
- Form Fill Queue: To Apply + `Fill Pack Status` beginning with `Ready`.
- Applications: `Status = To Review`.
- Follow-up Queue: `Queue State = Due` or `Data Quality - Missing Applied Date`.
- Interview Prep: Screening/Interview stages whose readiness tier is not Ready.

Safety:
- No Applications/pipeline writes.
- No User Action Center writes.
- No User Fact writes.
- No external-form writes.
- Medium/Structural readiness remains fail-closed.
