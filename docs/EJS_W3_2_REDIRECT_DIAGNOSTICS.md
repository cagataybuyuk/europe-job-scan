# W3.2.1 — Redirect diagnostics and bounded recovery

Status: offline implementation only. No live ATS request, redirect permission,
form access, upload, submission or ledger change is part of this work.

## Root-cause findings

1. `InspectionNetworkFence.route()` fetches every same-origin GET/HEAD request
   with `max_redirects=0` and denies **every** HTTP 3xx as
   `REDIRECT_UNREVIEWED`. It does not distinguish a normal same-origin document
   canonicalisation from a cross-origin redirect, a subresource redirect, an
   authentication redirect or a malformed `Location`.
2. `REDIRECT_UNREVIEWED` is in both the inspector and batch blocking-denial
   sets. One denied 3xx therefore turns an otherwise renderable observation into
   `NETWORK_FENCE_BLOCKED`.
3. The batch retry rule explicitly excludes `REDIRECT_UNREVIEWED` from temporary
   failures. The result is stored with `retry_eligible=false`, and later runs
   skip it as `already_processed`.
4. The old report/state contract kept only `network_denial_types`. It did not
   keep the redirect status, source/destination host, navigation type, path
   category or denial reason, so the exact redirect cannot be reconstructed.
5. Local pilot artifacts confirm this gap: five stored failures are
   `NETWORK_FENCE_BLOCKED` with
   `["REDIRECT_UNREVIEWED", "UNSUPPORTED_EXTERNAL_RESOURCE"]`, no access holds,
   and no redirect destination evidence. A separate form-inspection artifact
   shows the apply-entry stage returning `NETWORK_FENCE_BLOCKED` while the
   posting page itself had loaded, which points to the apply CTA or a
   post-CTA subresource rather than an initial posting-page failure.

`UNSUPPORTED_EXTERNAL_RESOURCE` is a non-blocking diagnostic in the batch. It
appears because cross-origin assets are denied by design; it is not, by itself,
the reason a form cannot be observed.

Remaining uncertainty: the old artifacts do not prove whether the 3xx was the
`/oneclick-ui/...` entry, a canonical posting-slug redirect, a same-origin API
call or another subresource. The new diagnostics are designed to answer that on
the next authorised read-only run. No CAPTCHA or access-hold evidence was found.

## Redirect diagnostic contract

Every denied 3xx now produces a bounded, sanitized record:

- `status`, `method`, `resource_type`, `navigation`
- `source_host`, `location_present`, `destination_valid`, `destination_host`
- `destination_trust_class`, `same_origin`, `destination_path_category`
- `scheme_transition`, `classification`, `deny_reason`
- `policy_candidate`, `permission_granted`, `access_restriction_evidence`,
  `review_status`

The raw `Location`, path, query, fragment and credentials are never retained.
External hosts are stored as `external_redacted`; trusted SmartRecruiters hosts
may be stored as hostnames. Paths are reduced to fixed categories such as
`posting_detail`, `oneclick_application`, `api`, `auth_or_account`, `asset`,
`company_page`, `root` or `external`.

`classification` distinguishes:

- `normal_ats_navigation_candidate` — same-origin, GET/HEAD, https-to-https
  document navigation to a posting-detail or oneclick path;
- `same_origin_navigation_unreviewed` — same-origin document navigation to a
  path outside the proposed narrow policy;
- `cross_origin_redirect`, `subresource_redirect`, `malformed_redirect` and
  `non_redirect_3xx`.

`policy_candidate` is a review label only. It is always
`permission_granted=false`, `access_restriction_evidence=false` and
`review_status="unreviewed"`.

The fence still denies every 3xx with `REDIRECT_UNREVIEWED`. The network
permission boundary is unchanged.

## Narrow policy proposal (requires orchestrator approval)

A future policy could follow at most one redirect hop when all of the following
hold:

- the request is a document navigation (`resource_type=document`);
- method is GET or HEAD;
- source and destination are the same origin;
- both schemes are HTTPS, with no credentials;
- the destination path category is `posting_detail` or
  `oneclick_application`;
- the destination is a trusted `*.smartrecruiters.com` host;
- the hop count is bounded to one and loops are rejected.

Everything else remains denied: cross-origin, auth/account paths, API paths,
subresource redirects, malformed or credential-bearing locations, and any
non-HTTPS transition. External resource denials remain non-blocking
diagnostics. This proposal is not enabled by W3.2.1.

## Recovery and next validation

Existing retry state remains unchanged. No automatic reset or recovery
writer is introduced. Obtain sanitized redirect evidence in a separately
authorized read-only inspection before reviewing any navigation correction.

A path category or candidate classification is not proof of the same
posting identity, an approved destination, or permission to follow it.
Any subsequent correction must preserve access controls, target identity,
rate limits and the canonical submission ledger.

## Offline test command

```sh
python -m pytest -q tests/test_w3_2_1_redirect_diagnostics.py \
  tests/test_application_batch.py tests/test_mvp02_network_evidence.py \
  tests/unit/test_smartrecruiters_live_inspector.py \
  tests/unit/test_smartrecruiters_live_route.py
```
