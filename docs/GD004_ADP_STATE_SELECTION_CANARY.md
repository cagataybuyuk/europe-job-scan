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


## Full Personal Information integration

After the first successful live State-selection run on 30 Sep 2026, the Personal Information safe-fill executor was updated so `State / Territory` is no longer treated as a text input after Country=Turkey.

The full executor now:
1. validates the pre-mutation Personal Information/contact contract;
2. writes reviewed identity/mobile facts;
3. selects reviewed Country=Turkey;
4. waits for ADP's State control rerender;
5. validates the reviewed State option-surface fingerprint;
6. selects exactly the profile's reviewed visible State label;
7. fills the remaining address text fields;
8. stops before consent, Next, upload, or Submit.

Because the Country selection intentionally changes the State DOM from a text input into a combobox, the old post-write assertion that the entire pre-country contact fingerprint must remain identical is no longer valid. Post-write assurance is instead composed from exact per-field readbacks, the reviewed Country readback, the reviewed State option-surface fingerprint, exact State label readback, and the existing phone readback contract.

No semantic conversion is allowed for State labels. For example, if ADP exposes `İstanbul`, the local reviewed profile must provide that exact visible label; the executor does not silently convert `Istanbul` to `İstanbul`.
