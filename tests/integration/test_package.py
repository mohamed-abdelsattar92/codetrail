"""The package PyPI receives: codetrail-cli, with the `codetrail` command and every file it ships (ADR 0019)."""

import subprocess
import zipfile
from email.parser import Parser
from pathlib import Path

from codetrail import __version__

REPOSITORY = Path(__file__).parents[2]


def build_wheel(destination: Path) -> zipfile.ZipFile:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(destination)], cwd=REPOSITORY, check=True, capture_output=True
    )
    (wheel,) = destination.glob("*.whl")
    return zipfile.ZipFile(wheel)


def test_the_wheel_is_codetrail_cli_with_the_codetrail_command(tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path)
    dist_info = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")).removesuffix("METADATA")
    metadata = Parser().parsestr(wheel.read(dist_info + "METADATA").decode())
    assert metadata["Name"] == "codetrail-cli"
    assert metadata["Version"] == __version__
    entry_points = wheel.read(dist_info + "entry_points.txt").decode()
    assert "codetrail = codetrail.cli:main" in entry_points


def test_the_wheel_holds_every_committed_file_of_the_package(tmp_path: Path) -> None:
    """The page's static files and the compiled catalogs included, so an install from PyPI is complete (ADR 0013)."""
    committed = subprocess.run(
        ["git", "ls-files", "src/codetrail"], cwd=REPOSITORY, check=True, capture_output=True, text=True
    ).stdout.split()
    shipped = {name for name in build_wheel(tmp_path).namelist() if name.startswith("codetrail/")}
    expected = {name.removeprefix("src/") for name in committed if not name.endswith(".po")}
    assert expected - shipped == set()
