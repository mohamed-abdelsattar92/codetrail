"""Tests for tools/pypi_readme.py: PyPI shows the README without the repository, so its paths must be absolute."""
import importlib.util
import pathlib
import re

spec = importlib.util.spec_from_file_location(
    "pypi_readme", pathlib.Path(__file__).resolve().parents[1] / "pypi_readme.py")
pypi_readme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pypi_readme)

REPOSITORY = "owner/project"
TAG = "v1.2.0"
RAW = "https://raw.githubusercontent.com/owner/project/v1.2.0/"
BLOB = "https://github.com/owner/project/blob/v1.2.0/"
README = pathlib.Path(__file__).resolve().parents[2] / "README.md"
# A Markdown link or image, or an HTML src, srcset or href, whose target has no scheme and isn't an anchor
RELATIVE_PATH = re.compile(r"\]\((?![a-z][a-z0-9+.-]*:|#)|(?:src|srcset|href)=\"(?![a-z][a-z0-9+.-]*:|#)")


def absolute(text: str) -> str:
    return pypi_readme.absolute_paths(text, REPOSITORY, TAG)


def test_images_point_at_the_raw_file_in_the_tag() -> None:
    assert absolute("![The home page](docs/images/home.png)") == f"![The home page]({RAW}docs/images/home.png)"


def test_html_images_point_at_the_raw_file_in_the_tag() -> None:
    html = '<source srcset="docs/images/logo-dark.svg">\n<img alt="Codetrail" src="docs/images/logo.svg">'
    assert absolute(html) == (f'<source srcset="{RAW}docs/images/logo-dark.svg">\n'
                              f'<img alt="Codetrail" src="{RAW}docs/images/logo.svg">')


def test_links_point_at_the_file_on_github_in_the_tag() -> None:
    assert absolute("see [the guide](docs/getting-started.md#9-use-it)") == (
        f"see [the guide]({BLOB}docs/getting-started.md#9-use-it)")


def test_leaves_absolute_urls_and_anchors_alone() -> None:
    text = "[PyPI](https://pypi.org/project/codetrail-cli/) [mail](mailto:a@example.com) [up](#install)"
    assert absolute(text) == text


def test_the_readme_has_no_relative_paths_left() -> None:
    assert RELATIVE_PATH.findall(absolute(README.read_text())) == []
