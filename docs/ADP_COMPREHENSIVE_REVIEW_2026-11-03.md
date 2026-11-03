# ADP PR #60 — comprehensive risk-based review, 2026-11-03

Verdict: **changes required; keep draft**. This is the implementing agent's review,
not an independent approval. No merge, release or production authority is granted.

## Scope and method

Reviewed the expanded PR by runtime flow and trust boundary: entry/verification,
session capture and replay, target/manifest binding, identity/contact/address,
custom Country/State controls, readiness, CLI/PowerShell entry points, workflow
permissions, profile policy and AWLI documentation. The file inventory below binds
the scope to file hashes. Test files were assessed for scenario coverage and run;
this is not a claim of exhaustive formal proof or every possible UI state.
PowerShell helpers were checked statically; the Mac live experiment uses Python,
not an assertion that Windows-only launchers run on macOS.

## Corrections from this review

- Unknown custom Country values no longer certify Turkey. Typed Turkey/TR requires
  a valid closed selection; keyboard and click paths accept the same reviewed modes.
- Contact semantic context retains exact reviewed labels only. Arbitrary ancestor
  prose can contain names/addresses/formatted phone text and is not safe evidence.
- Session state is validated in memory before export. Sensitive outputs are new
  exclusive files with POSIX mode 0600, refusing overwrites and symbolic links.
  POSIX permissions do not by themselves establish a Windows ACL guarantee.
- Observed OTP disappearance no longer authorizes capture on an unrelated form.
  Current authenticated target and explicit clear auth/challenge evidence are required.
- Direct/fallback replay proof requires authenticated target and clear current
  boundary evidence; arbitrary visible controls do not prove reuse.
- Session CLIs do not echo arbitrary browser/exception messages. Fallback proof
  no longer writes the full post-Apply URL into a diagnostic report.

## Remaining blocking findings

1. **P1 — mutation-time continuity in same-page canaries.** Identity/address,
   Country/State selection helpers authenticate the initial manifest/contract but
   do not revalidate the target and auth/challenge boundary before every later
   mutation. A redirect/rerender after the initial contract can invalidate it.
   Entry-autopilot per-field guards do not cover these other paths. Add a shared
   current-target/boundary guard at mutation boundaries, with redirect regressions.
   Do not run these mutation canaries live or mark the expanded PR ready yet.
2. **P1 — full configuration must precede first write.** The Personal Information
   executor invokes identity safe-fill before checking the requested contact
   fingerprint. A wrong downstream contract can therefore fail after name writes.
   Validate all available initial contracts before the first mutation, retaining
   staged checks for country-dependent State rerenders.
3. **P2 — replay policy compatibility.** Captured ADP postLogin URLs can contain
   identical repeated jobId parameters. Manifest/captured-URL checks accept this
   known same-valued case; the restoration script currently requires a single
   occurrence. This fails closed but may prevent diagnostic sessionStorage replay.
   Any normalization must prove all occurrences identical and preserve target
   binding; do not silently relax conflicting/blank query checks.
4. **P2 — platform/cleanup limits.** Several structural PowerShell probes require
   the Windows py launcher and best-effort cleanup. They are not Mac launchers;
   do not present them as such. Persistent-profile cleanup has a checked retry,
   while other helpers may need stronger checked cleanup/ACL handling on Windows.

## Live validation boundary

The requested live test uses a dedicated user-driven ADP browser, no automated
candidate writes/clicks, uploads, Next or Submit. The initial public job-details
screen is not authenticated-form evidence. The user authorized the existing EJS
technical target regardless of role suitability; this is not an application
recommendation or eligibility decision. Only a target-bound stable verified form
may be captured to process memory for fresh-browser read-only replay. No session
material is written to disk, GitHub, Sheets or an external API by this live harness.
A sanitized checkpoint records the actual result separately; pending/timeout is
not successful reuse. GitHub-runner reuse remains a separate unproven gate.

## Verification

After corrections: 239 ADP tests passed with Python socket connections blocked.
This includes private-file/no-overwrite/no-symlink cases, target/OTP/challenge
negative cases and actual generated JavaScript restoration cases. Earlier failed
fixture runs were corrected to provide explicit clear evidence; unknown evidence
was not made permissive. Remote CI must be checked for the final exact head.

## File inventory at review

Hashes cover the working-tree content of the reviewed files before this report
is committed; future changes require renewed review of the affected scope.

| File | SHA-256 | Review route |
|---|---|---|
| .github/workflows/gd004-adp-verified-session-regression.yml | 27e0b18f65b46a40941f06e06749089cb887a3c0b5c18c7adcccb0032a29dc55 | workflow trigger, permissions and test coverage |
| .gitignore | 5b22d39c53751284df5bf72a210928d1da26cef1fe6799cf24cc9cf9509b8601 | policy, configuration and claims |
| README.md | bf7b43113b48fa1b277570aa6221b24b9c593ae8c676555413bca067b0e8cb6c | policy, configuration and claims |
| docs/ADP_AWLI_FEASIBILITY_SPIKE.md | 43279ba248a9a4c078f5a7ddf27b17b563d22299e14f62917a0f2119c80b68eb | policy, configuration and claims |
| docs/ADP_REVIEW_CHECKPOINT.md | 967301bc82aea098b8b543bb68f6f5808bda1103acb5e1d32090538c41610ee7 | policy, configuration and claims |
| docs/GD004_ADP_PROFILE_POLICY.md | 752f6aaa8fe78bb31b172677f26385b41afb70eb33977f6abcb4f3ecf669618c | policy, configuration and claims |
| docs/GD004_ADP_STATE_SELECTION_CANARY.md | b289f99ac002041aa4ab6b88ca818bac6aa8483616fb1a5df34e5a07cb621a37 | policy, configuration and claims |
| docs/GD004_ADP_VERIFIED_SESSION_BOOTSTRAP.md | ca687af8b408f745942629f0eb17cfdbe66c9f639e99705542ff1fe5c64379d8 | policy, configuration and claims |
| docs/L0_COMMON_ATS_ADAPTER_CONTRACT.md | f34200703024138c4f307c9919b29ec501bb091da5d41639996b5e01d78000c8 | policy, configuration and claims |
| docs/PRODUCTION_MULTI_ATS_LAUNCH_PLAN.md | 975bd2b49aae53627663168681efcb0beb162878a89309ef8bf0a1f1a44960c6 | policy, configuration and claims |
| pyproject.toml | 8cbcc2920d36a249a3ef73aa4b50f9f63eeb6acc7f20f3d796dda1b2a61b4165 | policy, configuration and claims |
| scripts/bootstrap_adp_verified_session.ps1 | 49c5f53499dfa4196ce5b01e2af8cef321b408c2a91d8e4d5697f35661a85b01 | launcher, authority and cleanup |
| scripts/init_ejs_local_candidate_profile.ps1 | fcb040c9565844c73b1dcdd256a38522743803b3e464872c11cf27cf6defac35 | launcher, authority and cleanup |
| scripts/probe_adp_live_handoff.ps1 | bd5c6098ddd4b220ce670650aebc3e018cce03d5adc1c5d2bd84fc84dec57e31 | launcher, authority and cleanup |
| scripts/probe_adp_persistent_profile.ps1 | ce8bce898e3e41d059b26f9fa4bb6afdb28ed5e27646862d7279d77a42bb23a2 | launcher, authority and cleanup |
| scripts/probe_adp_same_page_contact_address.ps1 | 68c0c96e670c65871f528c0f8ace18d67af9cbe5c48befbaf714208f8d3289c6 | launcher, authority and cleanup |
| scripts/probe_adp_same_page_country_combobox.ps1 | 67bd2cff10e505f570c66bb42c9950c7188418842908661e4f212634fadac34e | launcher, authority and cleanup |
| scripts/probe_adp_same_page_country_dom_contract.ps1 | 093092f3eff16a547bed1ebf5c566cc40d8560386c401f2de0cea91b4c10d719 | launcher, authority and cleanup |
| scripts/probe_adp_same_page_country_keyboard_selection.ps1 | 948cde0405574463ba161a1ce79e04ab10632d6c5df43ff85d9bccc0208f805a | launcher, authority and cleanup |
| scripts/probe_adp_same_page_manifest.ps1 | 9f3b11d5fa1403175e2d3327813dab4885ff3d539df326413a5d2d80f09b671b | launcher, authority and cleanup |
| scripts/probe_adp_same_page_next_readiness.ps1 | 9bf4496a4511c16b6caecaff44c98bd4e89897dd21c72093d2861a722959cd13 | launcher, authority and cleanup |
| scripts/probe_adp_same_page_personal_information_safe_fill.ps1 | e10537ebae79d88876f2d4cebe64d0e8bae2f02431e609d944a08ebce5536351 | launcher, authority and cleanup |
| scripts/probe_adp_same_page_safe_fill.ps1 | f9b093aeb5d660130182d3b6f523af0d4cf9e0a0be4cb59da88182877c6f06e7 | launcher, authority and cleanup |
| scripts/probe_adp_same_page_state_after_country.ps1 | 942161dda8d373f191c438eeeadbc521a98b7099d03e209394b16320a7483e5b | launcher, authority and cleanup |
| scripts/probe_adp_same_page_state_dom_contract.ps1 | 43b6e320e7c51603f673fc65766fbb126d7858698d8dd0976d45aa570a68190c | launcher, authority and cleanup |
| scripts/probe_adp_same_page_state_selection.ps1 | 0def62b47e715c685d6be001163711cf8d2250beb19922d926ac02f1ccdaa450 | launcher, authority and cleanup |
| src/ejs/services/adp_entry_autopilot.py | 7676af0b99204fffce3217da0aec50699aadfd718c29129d231df0f61d936cbd | runtime trust boundaries |
| src/ejs/services/adp_persistent_profile_probe.py | 19f77b4ad713846e9712fb15965e8df4eca8871b444ece3227de6c39100a2bca | runtime trust boundaries |
| src/ejs/services/adp_personal_information_profile.py | ecf60d789adb948befaf9242c86ad7b27d87a8d4a4c7c78fff46def5e5e2e41c | runtime trust boundaries |
| src/ejs/services/adp_profile_v2_continue_canary.py | e219cbbadc33a42e9bd23dbd75412dd3c04dc71a945352999f24d40033c7c100 | runtime trust boundaries |
| src/ejs/services/adp_same_page_contact_address_contract.py | d30098297a2ba4371384f5f79db8e7feccdbe746f53ad6b6d25198edbe6bf0d0 | runtime trust boundaries |
| src/ejs/services/adp_same_page_country_combobox_probe.py | 765b6ebfe418a8be9f08c21662d9cfd7998596466d49c3ef43ea3ff19189ef8d | runtime trust boundaries |
| src/ejs/services/adp_same_page_country_dom_contract.py | a0554da9caf81f95805875492356d0b29f7e4efbe25cf2d366b62ca2534cee41 | runtime trust boundaries |
| src/ejs/services/adp_same_page_country_keyboard_selection_canary.py | 3d987489cda2b2cdc0ea7a2bccf2f4cd9ce88f2e786f96d765895ce32214e431 | runtime trust boundaries |
| src/ejs/services/adp_same_page_manifest.py | 6b04fbc72a7dce3a8c342dc2a4e51f8f3ae7e44b79aaa116589d696071f65774 | runtime trust boundaries |
| src/ejs/services/adp_same_page_next_readiness_canary.py | ad435983344da2daa4c5982b1c437e5340cfff8641984e9910dcd506f0a8e215 | runtime trust boundaries |
| src/ejs/services/adp_same_page_personal_information_safe_fill.py | 89c80d53b4004ec7c789c3968b1268c584d9482fe69ea060e776198894c68289 | runtime trust boundaries |
| src/ejs/services/adp_same_page_safe_fill.py | 96e9d326917f94f4c10b8013e6c3fa33492cce196b984ac341326a55fad06488 | runtime trust boundaries |
| src/ejs/services/adp_same_page_state_after_country_contract.py | 8c73abdb9089eb9668f25c0be00f26b4eb53eff34817aa960c707fbd12d4a1b9 | runtime trust boundaries |
| src/ejs/services/adp_same_page_state_dom_contract.py | 6987f842c1bcb9a42e3fff8b69a27083d022cc1a89dbb349514de3db8feba9ee | runtime trust boundaries |
| src/ejs/services/adp_same_page_state_selection_canary.py | 3fcede20fb4302214c1ed86328dd5d083fa3b116f18eedc7bc586253a03bf438 | runtime trust boundaries |
| src/ejs/services/adp_same_page_state_surface.py | cfcd80ac54f6d819b2249355678ddce96ce5536b4e34dc76a5f74825bf4ed50d | runtime trust boundaries |
| src/ejs/services/adp_session_restore_policy.py | da48a6e8f134548a2695c33f960dc2a368fbb6b3e0a738bb89a5514f78c1e798 | runtime trust boundaries |
| src/ejs/services/adp_verified_session_bootstrap.py | f3c5b630f9b78d87a04d5f665bdc241f8a2b98ec960154a480ef38dc70caf3b3 | runtime trust boundaries |
| src/ejs/services/adp_verified_session_inspector.py | 05639aff45d659e3d292134952bbf7cc38fb9fb73dd053dbf9b153a5ef9ea6dc | runtime trust boundaries |
| tests/scripts/test_gd004_bootstrap.ps1 | 98f7964bbe8eaf73df947a9ff67fb51a2061fe1f1f0443c2407bf03e78d431a5 | test coverage and execution |
| tests/unit/test_adp_entry_autopilot.py | 98f164b81277f6d4b21bb833704427b3507577e7931f8b7f6e891b460e44a699 | test coverage and execution |
| tests/unit/test_adp_personal_information_profile.py | 3a6f765df24521a690b7c68c40de98382ed66e5fd188564caab3713e7c7ba7cf | test coverage and execution |
| tests/unit/test_adp_same_page_contact_address_contract.py | 0dbec692045568841df183a018b1ab2f7aea21855cb8700654646c20ffc5a8ef | test coverage and execution |
| tests/unit/test_adp_same_page_country_combobox_probe.py | f1d3daebf98e89d66ca594bdf534d8582ccfcbd0f6438cba4a902d056cd4ffa7 | test coverage and execution |
| tests/unit/test_adp_same_page_country_dom_contract.py | 3d92b703bf15740a84e5c46b187905075db843b5bd99d0279906433d2c4d1281 | test coverage and execution |
| tests/unit/test_adp_same_page_country_keyboard_selection_canary.py | bdc9410c8217b7b317963e950e8a25f39f63ba27111a7aade96511ab3fb76d9f | test coverage and execution |
| tests/unit/test_adp_same_page_manifest.py | fdecfa92eb1b3a0bc06344716d178faa8a80bc7d64d2de7b6198518e9de567d1 | test coverage and execution |
| tests/unit/test_adp_same_page_next_readiness_canary.py | 56897c0a3f625ab8f1963d14ce7239e3beae0fc37c8f825d77b6207a42c4fb08 | test coverage and execution |
| tests/unit/test_adp_same_page_personal_information_safe_fill.py | 35d82b229b4d0115ad1769848e6eb13747cb1277f7d5d775b356e4257cd7a00c | test coverage and execution |
| tests/unit/test_adp_same_page_safe_fill.py | c3ed24164eecea4bec327b8d09c85711fe2d5d77f1bafa7f739c3c11ea72f9aa | test coverage and execution |
| tests/unit/test_adp_same_page_state_after_country_contract.py | 0bd6a2c33cdf68d2ac0cfd5c0918ea9d1bb25cc561ae21ca9ac526db37b7d955 | test coverage and execution |
| tests/unit/test_adp_same_page_state_dom_contract.py | ff43be4ce4726e82bb3d8260ce46b122707aa4e0570154343b225e569273e94a | test coverage and execution |
| tests/unit/test_adp_same_page_state_selection_canary.py | f654436eecffded32707f07e181ff48944e2293e1a49f0ade35055c377b6c286 | test coverage and execution |
| tests/unit/test_adp_session_restore_policy.py | 1c00abc51fabe221a1bed3d14c901134ef7965a40cecfc67db8a34481327aee1 | test coverage and execution |
| tests/unit/test_adp_verified_session.py | 8fa52176d551c242c0fba1dd4a5b112a87ff946e7fe6392c68c3bc8748fbd6e4 | test coverage and execution |
