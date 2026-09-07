# MW-0 / MW-1 Implementation Notes

## Freeze baseline

- Freeze ID: `EJS-MW0-20260817-01`
- Active rule bundle: `EJS-BUNDLE-1.4`
- Active contracts: 14
- Regression fixtures: 60
- Live invariants: 20
- Cross-surface mutation scenarios: 12
- Production write authority: none
- Browser execution: disabled

## MW-1 first implementation target

Create a Google Sheets read adapter that maps these surfaces into canonical snapshot objects:

1. Candidate Profile
2. Rule Registry
3. Applications
4. Source Targeting
5. CV Base Library
6. Application Answer Library

The first parity run must compare counts, keys, active versions and configuration values only. No discovery, Gmail outcome or pipeline mutation is permitted in the first shadow run.

## Exit gate

- environment guard tests pass;
- freeze manifest validation passes;
- production write path remains disabled;
- canonical snapshot loads deterministically;
- critical read/normalization parity is 100%.
