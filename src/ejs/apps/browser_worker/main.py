from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from enum import Enum

from ejs.contracts.browser import BrowserInspectionRequest, BrowserWorkerAuthority
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="BE-1 read-only Playwright browser inspector")
    parser.add_argument("url")
    parser.add_argument("--route-key", default="route:manual")
    parser.add_argument("--opportunity-key", default="opportunity:manual")
    parser.add_argument("--requisition-id", default="manual")
    parser.add_argument("--adapter-key", default="form:employer-custom")
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--chromium", default="")
    args = parser.parse_args()

    request = BrowserInspectionRequest(
        bridge_request_id=f"bridge:{args.route_key}:manual",
        route_key=args.route_key,
        opportunity_key=args.opportunity_key,
        requisition_id=args.requisition_id,
        application_url=args.url,
        adapter_key=args.adapter_key,
        observed_at=args.observed_at,
    )
    worker = PlaywrightBrowserWorker(BrowserRuntimeConfig(executable_path=args.chromium))
    result = worker.inspect(request, authority=BrowserWorkerAuthority())
    print(json.dumps(_jsonable(asdict(result)), ensure_ascii=False, indent=2))
    return 0 if result.read_only_invariant_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
