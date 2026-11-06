# ADP — Apply with LinkedIn Feasibility Spike

Status: planned; no live execution or AWLI capability claimed  
Planned host: personal MacBook Air M2  
Decision date: 2026-10-01  
Phase: L1 entry-strategy evaluation

## Objective and ownership

Determine whether Apply with LinkedIn (AWLI) can safely shorten ADP entry/prefill and earn primary-route implementation priority. Keep the native ADP flow as fallback. AWLI is neither LinkedIn Easy Apply nor external-apply URL resolution; the underlying ATS remains `adp`.

This spike plans evidence collection only. It does not broaden existing canary authority or approve social sign-in, field correction, Next, screening answers, CV upload, consent or Submit. Review any new entry/authorization actions separately before live execution.

## Preconditions on MacBook Air M2

- Confirm the exact repository revision, macOS/ARM64 Python/Playwright/browser versions and local/headful runtime readiness. Existing Windows PowerShell helpers are not evidence of macOS compatibility.
- Provision the private canonical candidate profile locally; keep it outside Git and never print its contents.
- Establish the personal LinkedIn session manually in a dedicated local browser profile. Do not migrate old-device browser/session credentials or assume ADP and LinkedIn state are interchangeable.
- Select one currently open, reviewed ADP requisition whose entry visibly offers AWLI. Bind both routes to the same employer, requisition and canonical apply URL. If AWLI is absent, record that outcome rather than infer support from the ATS brand.
- Check execution history/dedupe before opening either route. Plan isolated controlled browser contexts and account for server-side drafts created by entry. Do not repeat entry if duplicate or ambiguous application side effects cannot be excluded.
- Keep a bounded observation timeout, kill switch and explicit stop at stable Personal Information/application manifest before Next, upload or Submit.

## Comparison protocol

1. Capture the native route's current DOM/screen sequence from the reviewed job page: cookie boundary, Apply, identity/phone, Continue, email verification if present, pending submission/Complete Your Application if present, stable Personal Information. Treat this as a hypothesis based on earlier evidence; record the actual sequence. The user completes any required verification directly in the browser under the existing boundary.
2. From the same reviewed requisition, inspect AWLI availability and session readiness. Follow only separately reviewed entry/authorization actions. Missing session, login/MFA/CAPTCHA/security challenge or unexpected scopes stop AWLI; do not automate credentials or bypass the boundary.
3. Record the actual AWLI sequence, including redirects/popups, requested scopes, return target and any remaining ADP OTP/identity steps. Validate exact employer/requisition binding and stable non-empty returned ADP form; signed-in job details or a loading screen are insufficient.
4. Inspect returned prefills and compare locally against canonical candidate facts/approved policies. Record only field identifiers and sanitized match/mismatch/missing/locked/unknown outcomes. Reviewed name normalization and exact phone semantics remain governed by `GD004_ADP_PROFILE_POLICY.md`. Do not correct values during this inspection spike.
5. Stop at the application manifest and assess remaining CV, required questions, consent, review, submit and confirmation responsibilities without executing them.
6. Exercise missing/expired-session and challenge detection through sanitized fixtures or naturally observed boundaries; do not deliberately trigger account security challenges. Verify native fallback only after clean target/state reinspection proves no active challenge or ambiguous draft/submit side effects. Otherwise produce structured `human_review`.

## Evidence record

Use existing execution/opportunity IDs, target identity, source revision and evidence lineage. Record one comparison row per observed step:

| Evidence dimension | Native ADP | AWLI -> ADP |
| --- | --- | --- |
| Screen/DOM order and structural fingerprint | observed sequence | observed sequence |
| Entry/navigation actions and elapsed time to stable manifest | observed counts/time | observed counts/time |
| Identity/phone writes and manual interactions | observed counts | observed counts |
| Email OTP, LinkedIn session/MFA/challenge | observed/present/absent/unknown | observed/present/absent/unknown |
| Prefilled canonical-field coverage and verification | sanitized outcomes | sanitized outcomes |
| Return target, stable form and remaining required controls | validated/blocked | validated/blocked |
| Draft/duplicate effects and safe fallback | evidence/reason | evidence/reason |
| Fresh local replay/session behavior | proven/unproven | proven/unproven |

Keep screenshots/DOM evidence sanitized. Never commit raw candidate values, authorization codes, tokens, cookies, sessionStorage or browser profiles. Use control counts, fingerprints and policy-approved hashes/booleans consistent with existing privacy rules.

## Exit decision

- **Candidate for primary-route implementation:** exact reviewed AWLI entry reaches the correct stable ADP application manifest; prefills can be verified against canonical facts; action/manual-interaction/time comparison shows a useful reduction; auth/challenge detection and clean native fallback are fail-closed. Record remaining mismatches and separate implementation/canary work before promotion.
- **Keep native primary for the tested target:** AWLI is absent, no useful simplification is observed, or correctness/session reliability is weaker. Preserve the evidence and native fallback work.
- **Blocked / human_review:** challenge, unknown authorization, incorrect target, unresolved prefill or ambiguous side effects prevents a valid comparison. State the missing evidence; do not claim feasibility.

Report whether OTP was actually observed/removed/unchanged/unknown, which screens disappeared, which fields remain required, and what evidence is still needed. A single successful M2 run does not prove unattended daily operation, other employers/ATS families, persistent sessions or GitHub-runner reuse. Re-estimate L1 only after this result.

## Shared application gates

LinkedIn is never the source-of-truth. After verified entry/prefill, ADP still owns screening, work authorization/sponsorship/salary, consents, approved CV selection/upload evidence, remaining step navigation, SUBMIT-1, reviewed submit canary, confirmation reconciliation, dedupe and history. AWLI completion and Submit clicks cannot mark Applied/Confirmed.

References: [Launch plan](PRODUCTION_MULTI_ATS_LAUNCH_PLAN.md), [L0 contract](L0_COMMON_ATS_ADAPTER_CONTRACT.md), [Native bootstrap status](GD004_ADP_VERIFIED_SESSION_BOOTSTRAP.md), [Profile policy](GD004_ADP_PROFILE_POLICY.md).
