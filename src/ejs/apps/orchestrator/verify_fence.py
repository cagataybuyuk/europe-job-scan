from __future__ import annotations

import argparse
import json
from pathlib import Path

from ejs.services.read_fence import ReadFenceReport


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', default='config/live_connector_capture_2026-08-17.json')
    parser.add_argument('--post-fence', required=True)
    args = parser.parse_args()
    capture = json.loads(Path(args.capture).read_text(encoding='utf-8'))
    post = json.loads(Path(args.post_fence).read_text(encoding='utf-8'))
    report = ReadFenceReport(capture['read_fence'], post)
    print(json.dumps({'passed': report.passed, 'differences': report.differences}, indent=2))
    return 0 if report.passed else 3

if __name__ == '__main__':
    raise SystemExit(main())
