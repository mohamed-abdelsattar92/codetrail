"""Makes README.md's paths absolute for PyPI, which shows the README without the repository it came from.

publish.yml runs it before `uv build`: `python3 tools/pypi_readme.py <owner/repository> <tag>`. Images then load
from the release's tag on raw.githubusercontent.com and links open the file on GitHub at that tag. The README in
the repository keeps its relative paths, so GitHub shows each branch's own files.
"""
from __future__ import annotations

import pathlib
import re
import sys

# Not a URL with a scheme (https:, mailto:) and not an anchor in the same page
RELATIVE = r"(?![a-z][a-z0-9+.-]*:|#)"
MARKDOWN_PATH = re.compile(rf"(!?)(\[[^\[\]]*\]\()({RELATIVE}[^)\s]+\))")
HTML_IMAGE_PATH = re.compile(rf"((?:src|srcset)=\")({RELATIVE}[^\"]+\")")


def absolute_paths(text: str, repository: str, tag: str) -> str:
    raw = f"https://raw.githubusercontent.com/{repository}/{tag}/"
    blob = f"https://github.com/{repository}/blob/{tag}/"
    text = MARKDOWN_PATH.sub(lambda match: match[1] + match[2] + (raw if match[1] else blob) + match[3], text)
    return HTML_IMAGE_PATH.sub(lambda match: match[1] + raw + match[2], text)


def main() -> None:
    repository, tag = sys.argv[1:]
    readme = pathlib.Path("README.md")
    readme.write_text(absolute_paths(readme.read_text(), repository, tag))


if __name__ == "__main__":
    main()
