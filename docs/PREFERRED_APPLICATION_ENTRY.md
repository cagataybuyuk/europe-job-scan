# Preferred application entry — authorized TEST implementation

User scope update: 2026-11-03. Prefer exact Apply with LinkedIn when available;
use native entry when it is absent. This is deterministic policy, with no model
API or paid service. LinkedIn Easy Apply, profile links and Sign in with LinkedIn
are not AWLI entry actions.

`inspect_preferred_entry` inspects the existing ADP/SmartRecruiters page and returns
a value-free surface fingerprint and preference. `use_preferred_entry` requires
reviewed current fingerprint/target evidence, performs at most one exact AWLI
click and retains the caller's browser. It does not approve credentials, OAuth
grants, field writes, upload or submit. Duplicate/disabled/unscoped AWLI controls,
unknown boundaries, stale surfaces and attempts to retry fail closed.

After a click the caller must inspect the LinkedIn session/auth/scope boundary and
the returned ATS target, then verify all prefills against canonical facts. No
blind native fallback after AWLI and no assumption that returned values are true.
An unresolved login/permission/challenge boundary needs human review. The standard
read-only inspectors never gain click authority through this feature.

Existing-page adapter usage (not a self-approval mechanism):

```python
observed = inspect_preferred_entry(page, approved_application_url)
# Review the observation and provenance before supplying this fingerprint.
result = use_preferred_entry(
    page, approved_application_url,
    expected_surface_fingerprint=reviewed_fingerprint,
    counters={"awli_click_attempts": 0, "awli_click_successes": 0},
)
```

This is an operator entry adapter, not a deployment or a silent hook in the
existing production submit pipeline. Live AWLI and complete prefill coverage have
not yet been proved. It is developed as a separate TEST branch above PR #60.

The ADP entry function accepts an optional preferred-entry handler immediately
before native identity/phone writes. Its default is unchanged. The authorized
local operator supplies the new handler; an AWLI result prevents native identity,
phone and Continue actions, and an unclear preference fails closed. Production
read-only inspector workflows are not silently wired to clicks.

Local evidence: 262 selected network-blocked ADP/entry tests passed. A real
Chromium synthetic fixture selected AWLI once while native Apply remained unused;
all network requests were intercepted and no profile data was written. Live AWLI
remains unproven.
