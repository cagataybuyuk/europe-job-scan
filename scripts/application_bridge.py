"""Publish an owner-only preparation report using discovery and local fixtures."""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path

from shadow_discovery import private_outputs

from ejs.services.application_bridge import prepare_application_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discovery", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--fixtures", type=Path, help="JSON object mapping detail URLs to response objects")
    parser.add_argument("--detail-budget", type=int, default=0)
    args = parser.parse_args()

    raw = args.discovery.read_bytes()
    transport = None
    if args.fixtures:
        fixtures = json.loads(args.fixtures.read_bytes())
        if not isinstance(fixtures, dict):
            parser.error("fixtures must map detail URLs to response objects")

        def transport(url):
            return json.dumps(fixtures[url], ensure_ascii=False, sort_keys=True).encode("utf-8")

    report = prepare_application_report(json.loads(raw), transport=transport, detail_budget=args.detail_budget)
    report["detail_mode"] = "synthetic_fixtures" if args.fixtures else "none"
    report["discovery_sha256"] = sha256(raw).hexdigest()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    with ExitStack() as stack:
        (output,) = private_outputs(args.output_directory, stack, names=("preparation-report.json",))
        output.write(payload + "\n")
    print(json.dumps(report["readiness_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
