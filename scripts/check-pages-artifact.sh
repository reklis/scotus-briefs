#!/usr/bin/env bash
# Fail closed if a Pages output contains PDFs, unsafe links, or grows too large.
set -euo pipefail

artifact_dir=${1:-site/build}
max_mib=${2:-100}

if [[ ! $max_mib =~ ^[1-9][0-9]*$ ]]; then
  echo "Maximum artifact size must be a positive whole number of MiB: $max_mib" >&2
  exit 2
fi

python3 - "$artifact_dir" "$max_mib" <<'PY'
import os
from pathlib import Path
import stat
import sys

root = Path(sys.argv[1])
max_mib = int(sys.argv[2])
if not root.is_dir():
    raise SystemExit(f"Pages artifact directory does not exist: {root}")

size_bytes = 0
problems: list[str] = []


def walk_error(error: OSError) -> None:
    raise error


# Do not follow links. Inspect every entry, and propagate traversal/read errors;
# an unreadable artifact must never be treated as PDF-free.
for current, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
    current_path = Path(current)
    for name in [*directories, *files]:
        path = current_path / name
        relative = path.relative_to(root)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            problems.append(f"symbolic link is not allowed: {relative}")
            continue
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            problems.append(f"non-regular file is not allowed: {relative}")
            continue
        with path.open("rb") as stream:
            header = stream.read(1024)
        if path.suffix.lower() == ".pdf" or b"%PDF-" in header:
            problems.append(f"PDF is not allowed: {relative}")
        size_bytes += path.stat().st_size

if problems:
    for problem in problems:
        print(f"Pages artifact contains an unsafe entry: {problem}", file=sys.stderr)
    raise SystemExit(1)

max_bytes = max_mib * 1024 * 1024
if size_bytes > max_bytes:
    raise SystemExit(
        f"Pages artifact is {size_bytes} bytes; limit is {max_bytes} bytes ({max_mib} MiB)."
    )

print(f"Pages artifact verified: {size_bytes} bytes (limit {max_mib} MiB), no PDFs or links.")
output = os.environ.get("GITHUB_OUTPUT")
if output:
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(f"size_bytes={size_bytes}\n")
PY
