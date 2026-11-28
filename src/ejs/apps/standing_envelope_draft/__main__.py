"""W36-02 S4: draft the standing-envelope publication set (offline; publishes nothing).

    python -m ejs.apps.standing_envelope_draft --envelope E --index I --out DIR

Reads the owner's ``envelope.json`` and ``eligibility-index.json``, derives the
``current.json`` pointer and writes all three (0600, never overwriting) into
``DIR``: an existing directory owned by the caller, mode 0700, outside the
approval root. Prints file names and sha256 digests only. Publishing is the
owner's manual procedure (docs/STANDING_ENVELOPE_RUNBOOK.md); nothing here runs
sudo, writes as root or changes STOP/runtime.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ejs.services.execution_manifest import MAX_MANIFEST_BYTES


def _read(path: str) -> bytes:
    with open(path, "rb") as stream:
        return stream.read(MAX_MANIFEST_BYTES + 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft the standing-envelope files; publishes nothing.")
    parser.add_argument("--envelope", required=True, help="the owner's envelope.json draft")
    parser.add_argument("--index", required=True, help="the owner's eligibility-index.json draft")
    parser.add_argument("--out", required=True, help="existing 0700 directory owned by you")
    args = parser.parse_args(argv)

    from ejs.services.standing_envelope_draft import write_draft
    try:
        digests = write_draft(Path(args.out), _read(args.envelope), _read(args.index))
    except (OSError, PermissionError):
        print(json.dumps({"status": "REJECTED"}))
        return 2
    print(json.dumps({"status": "DRAFTED", "published": False, "sha256": digests}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
