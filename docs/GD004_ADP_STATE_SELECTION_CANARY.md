# GD-004 ADP State Selection Canary

## Purpose

This canary advances the verified same-page ADP Personal Information path by one bounded mutation: after the already reviewed Country=Turkey transition, select exactly one reviewed State/Territory label.

## Authority boundary

Allowed:
- re-run the reviewed Country selection needed to render the Turkish State dropdown;
- validate the State-after-Country contract fingerprint;
- validate the reviewed State option-surface fingerprint;
- match exactly one reviewed visible, enabled State label;
- click that option once;
- verify selection using visible combobox text.

Forbidden:
- reading option value attributes or DOM value properties;
- guessing/fuzzy matching a State;
- phone writes;
- address-line/city/postal-code writes;
- consent actions;
- Next;
- file upload;
- submit.

Any structural drift, fingerprint mismatch, duplicate/missing reviewed label, pre-existing conflicting State, or readback mismatch must fail closed.

## Live acceptance criteria

A successful report must show:
- `selection_status=verified` (or `already_committed` for an exact pre-existing match);
- `state_selection_attempts=1` and `state_selection_successes=1` for a new selection;
- `state_readback_match=true`;
- all non-State mutation counters remain zero;
- `raw_values_exposed=false`.

The first reviewed live target may be İstanbul, but the runtime is parameterized and must not hard-code a province name.

## Dependency

This canary depends on the captured State-after-Country contract from 30 Sep 2026. The observed State control rerenders from a text input to a combobox after Country=Turkey and exposes the reviewed Turkish province option surface.
