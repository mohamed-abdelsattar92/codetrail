"""Pre-commit check of staged files.

- No file over 1 MB outside the vendor folder, where Mermaid lives (ADR 0004).
Sizes are read from the staged blobs, not the working tree.
"""
from __future__ import annotations

import subprocess
import sys

MAX_BYTES = 1024 * 1024
ASSET_PREFIXES = ("src/codetrail/web/static/vendor/",)


def problems(path: str, size: int) -> list[str]:
    if size > MAX_BYTES and not path.startswith(ASSET_PREFIXES):
        return [f"{path}: {size / 1_048_576:.1f} MB is over the 1 MB cap outside {ASSET_PREFIXES[0]}"]
    return []


def staged_sizes(paths: list[str]) -> dict[str, int]:
    query = "".join(f":{p}\n" for p in paths)
    out = subprocess.run(["git", "cat-file", "--batch-check=%(objectsize)"], input=query,
                         capture_output=True, text=True, check=True).stdout.split()
    sizes = {}
    for path, value in zip(paths, out):
        sizes[path] = int(value) if value.isdigit() else 0
    return sizes


def main(paths: list[str]) -> int:
    if not paths:
        return 0
    found = [msg for path, size in staged_sizes(paths).items() for msg in problems(path, size)]
    for msg in found:
        print(msg, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
