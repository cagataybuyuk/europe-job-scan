# Standing envelope: owner runbook (W36-02 S4)

**Status: owner procedure.** This document describes the controls and publication steps.
Verify the current state locally before using them. Every command that needs `sudo`
is for the owner to type in their own Terminal after reading the exact bytes. No helper
in this repository runs `sudo`, writes as root, publishes, or changes `STOP`,
`runtime.json`, the tracker or a budget. Publishing an envelope does not remove `STOP`
and does not enable the runtime.

The engine (`docs/ENGINE_QUEUE.md`) submits an item only when **all** of these hold. The
runtime and standing approval checks re-read their inputs at admission and at every guard
point. The shared ledger separately checks duplicate and cap state during reservation and
checks the cap again before recording a start:

`STOP` absent **AND** `runtime.json` enables final submit **AND** the root-owned envelope is
valid **AND** the manifest is valid and recompiles byte-exactly **AND** inventory eligibility
recomputes as all-met **AND** the shared ledger allows it (no duplicate, cap not reached).

## 1. Approved pilot decisions (design constraints, not active settings)

The owner approved DEC-01–09 (AIW-96 `owner-envelope-decision`, revision a9755bfc).
They replace the plan-v2 "proposed defaults". Where the code already enforces a limit, the
envelope cannot widen it. "Procedure" means the owner applies it by review. "Gap" means it
is not yet implemented (see the S4 gap analysis).

| Decision | Requirement | Where it holds today |
|---|---|---|
| DEC-01 | Envelope valid at most 24 h | Code: `standing_envelope.py` rejects `expires_at - issued_at > 24 h`. |
| DEC-02 | Cap 1 per day, shared ledger, Europe/Istanbul day; renewal never resets it | Code: `daily_cap` must be exactly 1 and `daily_timezone` must be `Europe/Istanbul`. The ledger counts started, reserved and awaiting rows per Istanbul day, across every generation. |
| DEC-03 | SmartRecruiters only | Code: envelope, targets and manifest must be `smartrecruiters`. |
| DEC-04 | Deterministic eligibility from the verified inventory; missing, unknown, unmet or contradictory facts fail closed | Code: `inventory_eligibility.py` recomputes every mandatory line and ignores the package's own claims. |
| DEC-05 | Country = the country of the highest-ranked verified eligible vacancy, pinned before authorization | The offline preparer proposes the country from reviewed inputs. Admission checks that the attestation, package and envelope agree. |
| DEC-06 | Explicit pool of up to 5 verified vacancies in that country; no wildcard | The preparer, draft helper and trusted admission use the same ceiling. Admission also requires exact targets and rejects wildcard URLs and ids. |
| DEC-07 | Inventory built from the Fact Registry, CV and authorized local evidence; owner approves any new verified fact | Procedure. No inventory builder exists (gap). |
| DEC-08 | Automated evidence checks with exceptions; owner reviews the actual evidence and country attestations before authorization | Partly code (F05 detail checks; admission checks live-origin, freshness and hash links). Attestation review is this runbook's procedure. |
| DEC-09 | Narrow, time-limited first pilot; expansion only after success evidence, independent QA and explicit owner approval | Procedure. Only a new owner publication can widen scope. |

The pilot country and target list come from the ranked, verified pool (DEC-05/06).
`prepare_pilot_draft` can assemble a proposal from independently pinned F05, form,
attestation and inventory inputs. Review the proposed country and each exact target
before publication. Use at most five targets and leave unused slots empty.

## 2. Fixed limits (enforced by code, not settable in the envelope)

- **Schema:** `EJS-STANDING-ENVELOPE-1` with exactly these keys: `schema_version`,
  `envelope_id`, `generation`, `status`, `issued_at`, `expires_at`, `policy_version`,
  `candidate_profile_version`, `ats_family`, `country`, `targets`, `rollout_stages`,
  `daily_cap`, `daily_timezone`, `matching_rule_version`, `template_sha256s`,
  `policy_sha256`, `profile_sha256`, `cv_sha256`, `registry_sha256` and `inventory_sha256`.
  Unknown, missing or duplicate keys, floats and NaN are rejected.
- **Values:**
  - `status` is `active`.
  - `policy_version` is `SUBMIT-1.0`.
  - `ats_family` is `smartrecruiters`.
  - `daily_cap` is `1` (an integer, not `true`).
  - `daily_timezone` is `Europe/Istanbul`.
  - `matching_rule_version` is the code's `MATCHING_RULE`.
  - `rollout_stages` are bounded stages only.
  - `country` is one exact ISO code from the code's country table.
- **Pins:** every pin is a lowercase sha256 of exact bytes. The manifest's template must be
  in `template_sha256s`, and its target must be one of `targets`. Its daily cap and expiry
  cannot exceed the envelope's. The CV, registry and inventory must match the pins.
- **Index:** `EJS-BATCH-ELIGIBILITY-INDEX-1` maps `execution_id` to the sha256 of that
  execution's package `eligibility.json`. An execution that is not in the index is never
  admitted.
- **Pointer:** `EJS-STANDING-ENVELOPE-POINTER-1` (`current.json`) pins the envelope and
  index hashes and repeats `envelope_id` and `generation`. All three records must agree.
- **Files:**
  - **Approval root:** fixed at `/Library/Application Support/EJS/approval/`. There is no
    environment, argument or uid override.
  - **Directories:** every directory from `/` down to the root is owned by uid 0, is not
    group- or other-writable, and is not a symlink.
  - **Files:** owned by uid 0, mode `0440`, `0444`, `0640` or `0644`, one hard link, a
    regular file, no allow ACL, and within the size limit.
  - **Reads:** descriptor-based and re-verified after reading.
- **Per run:** the engine can launch Chromium before checking the queue. The item must
  pass admission before its browser context or page is created. Admission pins `envelope_id`,
  `generation` and the record hashes for that item. Every later guard point and the network
  boundary re-reads and re-admits. A newer generation never continues a pinned run.

## 3. What the owner reviews before publishing (DEC-07, DEC-08)

For every proposed execution, review the local package under
`~/Local/Projeler/EJS/engine-queue/packages/<execution id with ':' replaced by '~'>/`.
Use only the owner's own files; never paste them into tasks, logs or GitHub.

1. **`handoff.json`:** the F05 handoff. Read the actual employer evidence: company, posting,
   requisition, mandatory qualification lines, observation time. Its `evidence_origin`
   label and hash do **not** prove that it came from the employer. Compare it with the
   employer page yourself.
2. **`country-attestation.json`:**
   - Confirm `country` is the vacancy's real country, from the evidence, not from free-text
     location parsing.
   - Confirm `target` is the exact target.
   - Confirm `source_ref` is `sha256:` followed by the hash of that exact `handoff.json`.
     Check with `shasum -a 256 handoff.json`.
3. **`inventory.json`:** every entry used as `met` must be a fact you have verified. New
   facts are approved by you before `verified: true` is set (DEC-07). Nothing may be
   inferred or paraphrased.
4. **`eligibility.json`:** each mandatory line is listed exactly once, with `met` only where
   the inventory holds the exact text. The engine recomputes this and ignores the claims,
   but an honest `unknown` or `unmet` line means the vacancy is not eligible. Drop it from
   the pool.
5. **Choosing the pool:**
   - The country is the country of the highest-ranked verified eligible vacancy (DEC-05).
   - The pool is at most 5 targets in that country (DEC-06).
   - An execution whose evidence you cannot confirm is left out. It is never "fixed" by
     editing labels or hashes.

## 4. Drafting (offline, as yourself)

Write `envelope.json` and `eligibility-index.json` by hand in a private folder. Then derive
the pointer with the draft helper, which publishes nothing:

```sh
DRAFT="$HOME/ejs-envelope-draft-3"  # Example for generation 3; set to your draft path.
mkdir -m 700 "$DRAFT"
python -m ejs.apps.standing_envelope_draft \
  --envelope ~/ejs-drafts/envelope.json \
  --index ~/ejs-drafts/eligibility-index.json \
  --out "$DRAFT"
```

- **What it writes:** `envelope.json`, `eligibility-index.json` (your exact bytes, never
  reformatted) and `current.json`, all `0600`. It never overwrites a file.
- **What it refuses:**
  - running as root;
  - an output directory that is not yours with mode `0700`, or that is inside, equal to or
    a parent of the approval root. Use a physical path without symlinked directories. An
    access ACL that grants other access causes rejection;
  - malformed records or draftable envelope/index values outside the limits in section 2;
  - more than 5 targets, or a status other than `active`.
- **Output:** file names and sha256 digests only.

The helper lints the two input records. The engine checks the active time window, packages
and pins against the published files. Read the exact bytes with `cat` and compare
`shasum -a 256` with the printed digests before publishing.

## 5. Publishing (owner only, in Terminal; documentation, not automation)

Publish only while the engine is not running, with `STOP` present. Never at 08:30, when
launchd starts the engine. Check with `pgrep -fl ejs.apps.policy_engine`.

```sh
D="/Library/Application Support/EJS/approval"
S="$HOME/ejs-envelope-draft-3"  # Set to the reviewed draft path.
# Once: the root-owned directories (not group/other-writable).
sudo install -d -o root -g wheel -m 0755 "/Library/Application Support/EJS" "$D"
# Stage under temporary names in the same directory (same file system).
sudo install -o root -g wheel -m 0644 "$S/envelope.json"          "$D/.envelope.json.new"
sudo install -o root -g wheel -m 0644 "$S/eligibility-index.json" "$D/.eligibility-index.json.new"
sudo install -o root -g wheel -m 0644 "$S/current.json"           "$D/.current.json.new"
shasum -a 256 "$S/envelope.json" "$D/.envelope.json.new" \
  "$S/eligibility-index.json" "$D/.eligibility-index.json.new" \
  "$S/current.json" "$D/.current.json.new"
```

Confirm each pair has the same digest before running the rename commands. Keep `D` set
to the approval directory in the same Terminal.

```sh
# Publish with rename(2); the pointer goes last.
sudo mv -f "$D/.envelope.json.new"          "$D/envelope.json"
sudo mv -f "$D/.eligibility-index.json.new" "$D/eligibility-index.json"
sudo mv -f "$D/.current.json.new"           "$D/current.json"
# Verify: root:wheel, 644, one link, no ACL entries.
stat -f '%Su:%Sg %Lp %l %N' "$D" "$D"/*.json; ls -le "$D"
```

Each rename is atomic. The engine reads three files, and the pointer pins the other two
by hash. Between renames, any admission sees a pointer that does not match and **denies**.
It never admits a mixed set. This is why the pointer is renamed last, and why publication
happens with `STOP` present and the engine idle: a denial after `mark_started` leaves
`SUBMITTED_UNCONFIRMED`.

Enabling the runtime (`runtime.json`) and removing `STOP` are separate owner decisions,
outside this runbook's scope.

## 6. Revoke

```sh
touch ~/Local/Projeler/EJS/engine-queue/control/STOP   # immediate, local
sudo rm -f "/Library/Application Support/EJS/approval/current.json"   # durable, root-owned
```

- **Effect:** without the pointer, every admission and every later guard point denies.
- **The two commands differ:**
  - `STOP` is immediate, but the same uid as the agent can remove it.
  - The root-side removal is the durable revocation.
- **Not a revocation method:** publishing a `revoked` status. The validator rejects any
  non-active status, so it denies too, but the draft helper refuses to draft it.
- **What revocation cannot do:**
  - undo a completed network effect;
  - close the race between the last guard check and the employer's server;
  - resolve an item already past `mark_started`, which stays `SUBMITTED_UNCONFIRMED` and is
    never retried. Check the employer by hand.

## 7. Renew or change

Renewal means a new publication with a **new, larger `generation`**, new `issued_at` and
`expires_at` (at most 24 h apart), and packages re-issued with that generation. Each
package `eligibility.json` names its generation, and the index pins it.

- **In-flight items:** an item pinned to the old generation is denied at its next guard
  point. The next engine run admits items afresh.
- **The cap:** the shared ledger keeps counting across generations. Renewal never resets the
  daily cap, and a used `submit_key` stays a duplicate.
- **Rollback:** the generation alone is not anti-rollback against a root operator who
  restores old records. That is outside the threat model.
- **Narrowing:** a narrower scope (fewer targets, earlier expiry) is a new publication.
  Local narrowing (`STOP`, removing a package) only restricts. It can be undone by the same
  uid, so a durable narrowing is a root publication.
- **Expansion:** a wider pilot needs success evidence, independent QA and explicit owner
  approval under DEC-09. A new publication can change the country or selected targets
  within the supported limits. Another ATS, a higher cap or a window over 24 hours also
  requires changes to the enforced limits and review of those changes.

## 8. Residual same-uid risk (accepted by the owner)

The owner accepted that no root-owned copy of the engine is required. Under the agent's uid,
the agent can write all of the following:

- the engine code;
- the queue and `control/` (including `STOP` and `runtime.json`);
- the packages;
- the ledger;
- the CV folder.

The root-owned envelope bounds what an **honest** engine will admit: country, targets,
templates, pins, cap and expiry. It is not a sandbox against modified engine code running as
the same uid. Hash pins and `evidence_origin` labels prove byte integrity, not genuine
employer provenance or owner attestation. The owner's review in section 3 is what provides
those.

## 9. Tests (synthetic, offline)

- **S3 and E2E-01:** `tests/test_standing_guard.py` and `tests/test_standing_e2e.py` run the
  real engine queue, admission and trusted reader against a temporary approval tree. They
  present only that tree as root, through a test-only view of the reader module. They use a
  fake browser with a network fence.
- **Production arm:** the same synthetic bytes are **rejected** under the production
  trusted-source requirement (caller-owned files, or the real root path).
- **What an `EMPLOYER_CONFIRMED` in those tests means:** a fake acknowledgement inside a
  temporary ledger, never an employer-confirmed application.
- **Draft helper:** `tests/test_standing_envelope_draft.py`.
