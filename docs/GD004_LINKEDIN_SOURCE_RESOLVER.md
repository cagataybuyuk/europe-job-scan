# GD-004 LinkedIn Source Resolver

Status: read-only source discovery; no application mutation authority.

## Purpose

Europe Job Scan often receives LinkedIn job URLs even when the actual application is hosted by an employer ATS. LinkedIn is therefore treated as a discovery/source surface, not as an application execution target.

The resolver opens an approved public LinkedIn job-view URL in GitHub-hosted Chromium and inspects only the already-rendered public DOM needed to identify the application route.

## Resolution states

- `external_apply_resolved`: a direct external HTTPS application URL, or an external URL already embedded in a LinkedIn `/externalApply/` href query string, was discovered. The result becomes an ATS inspection candidate only.
- `linkedin_easy_apply`: Easy Apply is observed. This is a human-action boundary; the resolver does not automate LinkedIn Easy Apply.
- `login_boundary`: the public page requires LinkedIn sign-in/join flow before the route can be resolved.
- `challenge_boundary`: a visible security verification/CAPTCHA boundary is observed.
- `external_apply_unresolved`: LinkedIn exposes an external-apply wrapper but not the employer URL in the rendered href. The resolver does not click or follow the wrapper.
- `apply_target_not_discovered`: no trustworthy application target is exposed in the read-only snapshot.

## Authority boundary

The resolver has no operation for:

- Apply/Easy Apply clicks,
- field writes,
- file uploads,
- credential entry,
- consent actions,
- CAPTCHA solving or bypass,
- final Submit.

Every live workflow run asserts zero click, write, upload, credential-entry and submit attempts. Even when an external target is resolved, `automation_execution_ready` remains false. The external URL must pass a separate ATS read-only inspection and reviewed manifest gate before any safe-fill canary.

## Privacy / evidence

Artifacts contain only the source URL, final public URL/title, small structural counters, coarse boundary signals, allowlisted Apply anchor metadata, and a resolved external HTTPS target when exposed. The resolver does not persist page HTML, cookies, local/session storage, candidate data, credentials, or form values.

## Live workflow

Use `.github/workflows/gd004-linkedin-source-resolution.yml` with:

- the exact immutable `main` SHA,
- the approved LinkedIn job-view URL,
- approval phrase `APPROVE-TEST-LINKEDIN-SOURCE-RESOLUTION`.

If the result is `external_apply_resolved`, the next step is to classify the external host/ATS and run the appropriate read-only inspector. No source-resolution result authorizes safe-fill or submit by itself.


## Production-scope revision — 2026-09-18

The first production launch now includes a **separate LinkedIn Easy Apply adapter** in addition to this external-source resolver.

This document continues to define the current resolver's authority: it remains read-only and does not itself gain Easy Apply click/write/submit operations. The new production architecture treats LinkedIn as two distinct paths:

1. **External Apply:** this resolver discovers the external target, the ATS classifier identifies SmartRecruiters / Workday / Greenhouse / Lever / ADP / other, and the corresponding ATS adapter owns execution.
2. **Easy Apply:** a dedicated session-aware adapter owns modal inspection, verified field writes, approved document attachment, policy-backed screening answers and conditional submit.

The Easy Apply adapter must stop at login, MFA, CAPTCHA/security verification, account-recovery or unknown required-question boundaries. No resolver result and no existing LinkedIn session alone authorizes final submit.

Launch acceptance and phased implementation are defined in `PRODUCTION_MULTI_ATS_LAUNCH_PLAN.md`.
