# SmartRecruiters policy browser (GD-006 A1, step 4b-2)

`SmartRecruitersPolicyBrowser` implements `PolicyFormBrowser` (see `POLICY_SUBMISSION.md`)
for SmartRecruiters one-click applications. It owns a fresh Playwright context with no
user cookies, blocks service workers, WebSockets and popups, and routes every request
through `PolicyNetworkBoundary`.

## Network boundary

| Request | Allowed |
|---|---|
| Document navigation | the posting page `/<company>/<posting_id>[-slug]`, then the publication's entry, `/screening` and `/success` pages with `?dcr_ci=<company>` |
| Other GET/HEAD | HTTPS on reviewed read hosts only (default `jobs.smartrecruiters.com`; asset hosts are added after the first real Mac run) |
| CV upload | one POST to `/oneclick-ui/api/company/<24-hex>/attachment/resume?filename=<approved file>` during the upload phase |
| Application | one POST to the publication's `/application` endpoint, armed only after the runtime committed `mark_started`; a premature attempt closes it for good |

Everything else is blocked and counted; any blocked write makes the form "not ready".
In production every allowed request is performed by the browser itself with redirects
disabled (`route.fetch(max_redirects=0)`); a 3xx answer is aborted and counted, so no
redirect hop can bypass the boundary. Any observed redirect hop or service worker makes
the session fatal. Offline tests use `transport="fallback"`.

Known limits: the CV upload's 24-hex company id is not pinned in advance (only one upload
with the approved file name is possible); any iframe counts as a challenge, and CAPTCHA
providers are not on the read list, so such runs usually end unresolved for the owner.
The publication id is read from the single one-click link on the posting page.

## Control keys (shared with Claude's inspection)

The key of a control is derived from its visible label with `control_key`: NFKC,
casefold, every run of characters other than ASCII letters and digits becomes `-`,
trimmed, at most 100 characters. Example: `"Expected salary (EUR)"` →
`expected-salary-eur`. Two controls with the same key on one page fail closed.
Option lists are the visible option texts as shown (including placeholders such as
"Choose"). Claude must build manifests with the same rule.

## Controls

text, email, tel, number/search/url (as `text`), textarea, native select, combobox
with an `aria-controls` listbox of `role="option"` items, radio groups and checkboxes.
Anything else (for example phone country pickers or address autocompletes without a
listbox) fails closed with `POLICY_APP_SR_CONTROL_UNSUPPORTED`.

## Challenges

An iframe, a popup, a WebSocket or challenge wording ("verify you are human", "verification
code", ...) marks the page as a challenge. The runtime then waits for the owner, who
solves it in the visible window; the browser only observes. After the handoff the page
URL must be unchanged.

## Not yet verified on a live page

The tests use offline pages modelled on the reviewed one-click flow. The first real Mac
run (step 6/8) must confirm read hosts, labels, the upload acknowledgement and the
success URL before automatic use.
