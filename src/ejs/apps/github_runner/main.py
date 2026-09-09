from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
import re
import sys

from ejs.contracts.github_executor import ExecutionProvider
from ejs.services.github_executor import GitHubReadOnlyBrowserExecutor, fixture_url, sanitized_output

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _env_true(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


def _assert_gd004_runtime_fence() -> None:
    if not _env_true("EJS_LIVE_READ_ONLY", "true"):
        raise PermissionError("GD-004 requires EJS_LIVE_READ_ONLY=true")
    forbidden = [
        name
        for name in ("EJS_FORM_WRITE", "EJS_FILE_UPLOAD", "EJS_FINAL_SUBMIT")
        if _env_true(name, "false")
    ]
    if forbidden:
        raise PermissionError(f"GD-004 forbidden runtime flags enabled: {', '.join(forbidden)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="GD-004 zero-cost GitHub browser executor")
    parser.add_argument("--url", default="")
    parser.add_argument("--fixture", default="")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--provider", default=os.environ.get("EJS_BROWSER_PROVIDER_TEST", "github_hosted"))
    parser.add_argument("--request-id", default="request:gd004:manual")
    parser.add_argument("--execution-id", default="execution:gd004:manual")
    parser.add_argument("--opportunity-key", default="opportunity:gd004-test")
    parser.add_argument("--requisition-id", default="gd004-test")
    parser.add_argument("--adapter-key", default="form:employer-custom")
    parser.add_argument("--observed-at", default="")
    args = parser.parse_args()

    _assert_gd004_runtime_fence()
    if not _SHA_RE.fullmatch(args.source_sha):
        raise ValueError("--source-sha must be an immutable 40-char lowercase git SHA")
    if bool(args.url) == bool(args.fixture):
        raise ValueError("provide exactly one of --url or --fixture")

    provider = ExecutionProvider(args.provider)
    application_url = args.url or fixture_url(args.fixture)
    observed_at = args.observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    executor = GitHubReadOnlyBrowserExecutor(provider=provider)
    result, evidence = executor.inspect(
        application_url=application_url,
        observed_at=observed_at,
        source_sha=args.source_sha,
        request_id=args.request_id,
        execution_id=args.execution_id,
        opportunity_key=args.opportunity_key,
        requisition_id=args.requisition_id,
        adapter_key=args.adapter_key,
    )
    print(sanitized_output(result, evidence))
    if any((result.mutation_count, result.upload_count, result.submit_count)):
        return 3
    return 0 if evidence["read_only_invariant_ok"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GD004_EXECUTOR_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
