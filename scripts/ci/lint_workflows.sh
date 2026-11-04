#!/usr/bin/env bash
# Check ALL workflows, including workflow_dispatch-only files not executed by PR CI.
set -euo pipefail
version=1.7.12
platform="$(uname -s)_$(uname -m)"
case "$platform" in
  Linux_x86_64)
    archive="actionlint_${version}_linux_amd64.tar.gz"
    digest=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
  Darwin_arm64)
    archive="actionlint_${version}_darwin_arm64.tar.gz"
    digest=aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f ;;
  Darwin_x86_64)
    archive="actionlint_${version}_darwin_amd64.tar.gz"
    digest=5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644 ;;
  *) echo 'Unsupported actionlint platform' >&2; exit 2 ;;
esac
lint_tmp="$(mktemp -d "${TMPDIR:-/tmp}/ejs-actionlint.XXXXXX")"
trap 'rm -rf -- "$lint_tmp"' EXIT
curl --fail --silent --show-error --location --connect-timeout 10 --max-time 60 --retry 2 \
  "https://github.com/rhysd/actionlint/releases/download/v${version}/${archive}" \
  --output "$lint_tmp/tool.tar.gz"
python3 - "$lint_tmp/tool.tar.gz" "$digest" <<'PY'
import hashlib, pathlib, sys
if hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit('actionlint checksum mismatch')
PY
tar -xzf "$lint_tmp/tool.tar.gz" -C "$lint_tmp" actionlint
# Expression, YAML, context and action validation are independent of optional
# shellcheck/pyflakes availability. No workflow is executed by this linter.
"$lint_tmp/actionlint" -shellcheck='' -pyflakes=''
