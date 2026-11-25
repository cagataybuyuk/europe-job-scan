"""Report ATS inspection coverage from a local Employer Watchlist CSV export."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from ejs.contracts.ats_adapter import DispatchState
from ejs.services.ats_adapter_registry import REGISTRY_VERSION, classify_application_target

_HEADER = ("Employer", "Primary Market", "Reference Vacancy / Careers URL")
_OTHER_ATS_HOSTS = {
    "recruitee.com": "recruitee",
    "workable.com": "workable",
    "hirehive.com": "hirehive",
}
_STATUSES = ("supported", "unsupported", "unknown")


@dataclass(frozen=True)
class CoverageRow:
    employer: str
    market: str
    host: str
    provider: str
    status: str
    detail: str


def classify_employer(employer: str, market: str, url: str) -> CoverageRow:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".")
        if (
            parsed.scheme != "https"
            or not host
            or parsed.port == 0
            or parsed.username is not None
            or any(char.isspace() or char == "\\" for char in url)
        ):
            raise ValueError("unusable reference URL")
    except ValueError:
        return CoverageRow(employer, market, "", "", "unknown", "No usable HTTPS reference")

    decision = classify_application_target(url)
    if decision.reason_code == "LINKEDIN_ROUTE_REQUIRES_SOURCE_RESOLUTION":
        return CoverageRow(employer, market, host, "", "unknown", "LinkedIn destination unresolved")
    if decision.state is not DispatchState.UNSUPPORTED:
        status = "supported" if decision.state is DispatchState.DISPATCH else "unsupported"
        return CoverageRow(
            employer, market, host, decision.family.value, status,
            decision.implementation_state.value,
        )
    for suffix, provider in _OTHER_ATS_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return CoverageRow(employer, market, host, provider, "unsupported", "No registry adapter")
    return CoverageRow(employer, market, host, "", "unknown", "No verified ATS host")


def read_coverage(source: Path) -> list[CoverageRow]:
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        for fields in reader:
            if tuple(field.strip() for field in fields) == _HEADER:
                break
        else:
            raise ValueError("Employer Watchlist header not found")

        rows = []
        seen = set()
        for fields in reader:
            fields = [field.strip() for field in fields]
            if not any(fields):
                continue
            if fields == ["Priority Employer Scan Queue", "", ""]:
                break
            if len(fields) != len(_HEADER) or not fields[0] or not fields[1]:
                raise ValueError(f"Invalid employer row at CSV line {reader.line_num}")
            employer, market, url = fields
            key = employer.casefold()
            if key in seen:
                raise ValueError(f"Duplicate employer at CSV line {reader.line_num}")
            seen.add(key)
            rows.append(classify_employer(employer, market, url))
        if not rows:
            raise ValueError("Employer Watchlist section is empty")
        return rows


def _markdown_cell(value: str) -> str:
    value = escape(" ".join(value.split()))
    for char in "\\|`*_[]~":
        value = value.replace(char, "\\" + char)
    return value


def render_markdown(rows: list[CoverageRow]) -> str:
    counts = Counter(row.status for row in rows)
    gaps = Counter(row.provider for row in rows if row.status == "unsupported")
    lines = [
        "# Employer Watchlist Coverage",
        "",
        f"Inspection support from {REGISTRY_VERSION}. Evidence is the host in the local CSV.",
        "Employer-level results describe the supplied reference only.",
        "",
        f"- Employers: {len(rows)}",
        *(f"- {status.capitalize()}: {counts[status]}" for status in _STATUSES),
        f"- Coverage: {counts['supported'] / len(rows):.1%}",
        "",
        "## Missing inspection support by ATS",
        "",
        *([f"- {provider}: {count}" for provider, count in sorted(gaps.items())] or ["None identified."]),
        "",
        "## Employer references",
        "",
        "| Employer | Primary market | Reference host | ATS | Status | Adapter state or finding |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        values = (row.employer, row.market, row.host, row.provider, row.status, row.detail)
        lines.append("| " + " | ".join(_markdown_cell(value) or "—" for value in values) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("watchlist", type=Path, help="Local Employer Watchlist CSV export")
    args = parser.parse_args()
    try:
        rows = read_coverage(args.watchlist)
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))
    print(render_markdown(rows), end="")


if __name__ == "__main__":
    main()
