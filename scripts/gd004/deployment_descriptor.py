from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Iterable

DEPLOYMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if "deploymentId" in value or "deployment_id" in value:
            yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _description(item: dict[str, Any]) -> str:
    config = item.get("deploymentConfig") or item.get("deployment_config") or {}
    if isinstance(config, dict):
        return str(config.get("description") or "")
    return str(item.get("description") or "")


def _deployment_id(item: dict[str, Any]) -> str:
    return str(item.get("deploymentId") or item.get("deployment_id") or "")


def _web_url(item: dict[str, Any], deployment_id: str) -> str:
    entry_points = item.get("entryPoints") or item.get("entry_points") or []
    if isinstance(entry_points, list):
        for entry in entry_points:
            if not isinstance(entry, dict):
                continue
            web_app = entry.get("webApp") or entry.get("web_app") or {}
            if isinstance(web_app, dict) and str(web_app.get("url") or "").startswith("https://"):
                return str(web_app["url"])
    return f"https://script.google.com/macros/s/{deployment_id}/exec"


def select_deployment(payload: Any, *, description_prefix: str) -> tuple[str, str]:
    candidates: list[dict[str, Any]] = []
    for item in _walk(payload):
        deployment_id = _deployment_id(item)
        if not DEPLOYMENT_ID_RE.fullmatch(deployment_id):
            continue
        if _description(item).startswith(description_prefix):
            candidates.append(item)
    if not candidates:
        raise ValueError(f"no deployment found with description prefix {description_prefix!r}")
    # Apps Script deployment listings are chronological in practice; choosing the last matching
    # item is deterministic for a single TEST project and remains bounded by the description prefix.
    selected = candidates[-1]
    deployment_id = _deployment_id(selected)
    return deployment_id, _web_url(selected, deployment_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--description-prefix", required=True)
    parser.add_argument("--input", default="-")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()

    if args.input == "-":
        payload = json.load(sys.stdin)
    else:
        with open(args.input, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

    deployment_id, web_url = select_deployment(payload, description_prefix=args.description_prefix)
    if args.github_output:
        print(f"deployment_id={deployment_id}")
        print(f"web_app_url={web_url}")
    else:
        print(json.dumps({"deployment_id": deployment_id, "web_app_url": web_url}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
