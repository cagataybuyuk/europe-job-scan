"""Publish an owner-only preparation report using discovery and local fixtures."""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from shadow_discovery import private_output_directory, require_no_extended_acl

from ejs.services.application_bridge import prepare_application_report


def publish_report(directory: Path, payload: bytes) -> None:
    with ExitStack() as stack:
        directory_fd = private_output_directory(directory, stack)
        pending = f".preparation-{uuid4().hex}.tmp"
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                require_no_extended_acl(output.fileno())
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(pending, "preparation-report.json", src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd, follow_symlinks=False)
            except FileExistsError as exc:
                raise ValueError("preparation report files already exist") from exc
        finally:
            os.unlink(pending, dir_fd=directory_fd)


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
    publish_report(args.output_directory, (payload + "\n").encode("utf-8"))
    print(json.dumps(report["readiness_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
