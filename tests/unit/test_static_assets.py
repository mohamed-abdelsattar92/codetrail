"""Bundled assets: Inter matches the sums in its VERSION file, and the mark's SVGs are inert (ADR 0007, 16.2)."""

import hashlib
import re
from importlib.resources import files
from pathlib import Path
from xml.etree import ElementTree

import pytest

STATIC = Path(str(files("codetrail.web").joinpath("static")))
REPOSITORY = Path(__file__).parents[2]
SVGS = [STATIC / "brand" / "mark.svg", STATIC / "brand" / "favicon.svg", REPOSITORY / "docs" / "images" / "logo.svg"]


@pytest.mark.parametrize(
    ("folder", "fonts", "licence"),
    [
        ("inter", {"InterVariable.woff2", "InterVariable-Italic.woff2"}, "LICENSE.txt"),  # ADR 0007
        ("noto-sans-arabic", {"NotoSansArabic-wght.woff2"}, "OFL.txt"),  # ADR 0014
    ],
)
def test_the_font_files_match_their_recorded_sums(folder: str, fonts: set[str], licence: str) -> None:
    vendor = STATIC / "vendor" / folder
    recorded = dict(re.findall(r"([0-9a-f]{64})\s+(\S+\.woff2)", (vendor / "VERSION").read_text()))
    assert set(recorded.values()) == fonts
    for digest, name in recorded.items():
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest, name
    assert "SIL Open Font License" in (vendor / licence).read_text()


@pytest.mark.parametrize("svg", SVGS, ids=lambda path: path.name)
def test_the_marks_are_inert_svg(svg: Path) -> None:
    root = ElementTree.fromstring(svg.read_text())  # noqa: S314 - Codetrail's own files, not untrusted input
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    for element in root.iter():
        assert not element.tag.endswith(("script", "foreignObject", "image", "use"))
        for name, value in element.attrib.items():
            assert not name.lower().startswith("on"), name
            assert "http" not in value or name == "xmlns", (name, value)
            assert "javascript:" not in value.lower()


def test_axe_core_matches_its_recorded_sum() -> None:
    vendor = REPOSITORY / "tests" / "browser" / "vendor"
    [digest] = re.findall(r"([0-9a-f]{64})\s+axe\.min\.js", (vendor / "VERSION").read_text())
    assert hashlib.sha256((vendor / "axe.min.js").read_bytes()).hexdigest() == digest
    assert "Mozilla Public License" in (vendor / "LICENSE").read_text()
