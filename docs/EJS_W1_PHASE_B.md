# EJS-W1 Phase B read-only pilot

Run after independent review of the Rev 3 commit, owner approval of the criteria
file and live pilot, and verification that at least 9 GiB is free and has stayed
above that threshold for 30 minutes. Keep the approved criteria and pilot output
in an owner-only directory outside the repository. The criteria file must meet
the versioned criteria loader contract. The existing AIW-6 draft is awaiting
approval and has no `rule_version`.

Set `EJS_W1_WORKTREE` to the reviewed worktree and `EJS_W1_PRIVATE` to the
owner-only directory that holds the approved criteria. Save this exact
`pilot-watchlist.csv` in the private directory.

```csv
Employer,Primary Market,Reference URL
HSO International,Netherlands,https://job-boards.eu.greenhouse.io/hsogroupbv
CarTrawler,Ireland,https://jobs.lever.co/cartrawler
Veepee,Spain,https://jobs.lever.co/veepee
Rituals,Netherlands,https://jobs.smartrecruiters.com/Rituals1
```

```sh
(
  set -eu
  umask 077
  cd "$EJS_W1_WORKTREE"
  pilot_out=$(mktemp -d "$EJS_W1_PRIVATE/phase-b.XXXXXX")
  printf 'Pilot output directory: %s\n' "$pilot_out"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" \
    "$HOME/europe-job-scan/.venv/bin/python" scripts/shadow_discovery.py \
    --watchlist "$EJS_W1_PRIVATE/pilot-watchlist.csv" \
    --criteria "$EJS_W1_PRIVATE/criteria.approved.yaml" \
    --out-dir "$pilot_out" \
    --live
)
```

## Acceptance

- Exit code 0 and four expected employer entries in `report.json`. Each entry
  must be `complete` or `empty`. A failure or an incomplete feed must return
  a nonzero status and remain visible in the report.
- Every vacancy has its employer, ATS, region, tenant, posting ID, title,
  location when available, canonical public URL, feed evidence and criteria
  result. A vacancy with unknown identity remains in the report and fails the
  live pilot. Different posting IDs remain distinct.
- The request log contains at most five requests per employer and twenty in
  total. Request starts are at least one second apart. The pilot uses only
  public HTTPS GET on `boards.eu.greenhouse.io`, `api.lever.co` and
  `api.smartrecruiters.com`. Redirects, proxies, credentials, cookies and
  retries are disabled.
- Evidence consists of `report.json`, `report.md` and `request_log.json`.
  The pilot persists no raw response bodies and performs no employer-facing
  writes, application submissions, CV uploads or tracker writes.

[Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html),
[Lever Postings API](https://github.com/Lever/postings-api) and
[SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/endpoints)
describe the feed formats. Greenhouse's EU API route on
`boards.eu.greenhouse.io` was verified using a nonexistent synthetic board
token. The endpoint returned a JSON 404 and `x-farm-id: eu`. The HSO board
itself remains to be verified in the pilot.

Offline fixture replay reads `<fixture-dir>/<api-host>/<tenant>/<offset>.json`.
Use offset `0` for the first page. A missing host, tenant or offset fixture is
reported as `missing_fixture`. Replay uses a fixed clock for deterministic
reports and request logs.
