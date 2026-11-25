# Employer Watchlist Coverage

Run from the repository root with Python 3.11 or later.

```sh
PYTHONPATH=src python -m ejs.apps.watchlist_coverage '/path/to/Employer watchlist - Sheet1.csv'
```

Pass the local CSV export of the owner-maintained Employer Watchlist. The report
goes to stdout as Markdown, so it can be saved with shell redirection. The
export may contain a preamble and a trailing Priority Employer Scan Queue.
The three-column employer section supplies the employer, market and reference URL.
The CLI counts data rows and keeps their source order. The raw export stays
outside the repository.

The reference hostname is the evidence shown for each row. URL paths and
query strings are omitted. The report uses the existing ATS registry for
known launch providers and recognizes the Recruitee, Workable and HireHive
domains in the source export.

| Status | Meaning |
| --- | --- |
| supported | The reference maps to an inspection-capable adapter in the registry. |
| unsupported | The provider is identified and has a planned registry entry or lacks an inspection-capable registry adapter. |
| unknown | The supplied reference gives insufficient ATS evidence, including unresolved LinkedIn routes, custom career sites and missing URLs. |

Coverage is the share of all listed employers whose supplied reference maps
to an inspection-capable adapter. Custom domains, employer migrations, redirects
and other vacancies may require additional evidence. The report makes no live
requests and grants no application authority.

## Observed baseline

The locally available `Employer watchlist - Sheet1.csv` export contains 37
employers. Its references reproduce the four employers from the AIW-6
classification within the AIW-15 Phase 1b discovery scope: HSO International
(Greenhouse), CarTrawler and Veepee (Lever), and Rituals (SmartRecruiters).
On EJS base `0180adc`, the current inspection registry covers Rituals.
Greenhouse and Lever are planned there. The full report gives 1 supported,
10 unsupported and 26 unknown (2.7% inspection coverage).

For integration, retain the export as a local input and run the CLI from the
reviewed checkout. Registry changes will be reflected by subsequent reports.
The CLI has no runtime wiring and uses the Python standard library plus the
existing read-only classification API.
