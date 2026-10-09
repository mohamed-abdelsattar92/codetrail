"""The right-to-left test catalog, compiled where a test's page can find it (design section 7.2)."""

from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

FIXTURE_LOCALES = Path(__file__).resolve().parent / "locales"


def compiled_locales(folder: Path) -> Path:
    target = folder / "ar" / "LC_MESSAGES"
    target.mkdir(parents=True, exist_ok=True)
    with (FIXTURE_LOCALES / "ar" / "LC_MESSAGES" / "codetrail.po").open("rb") as source:
        catalog = read_po(source)
    with (target / "codetrail.mo").open("wb") as compiled:
        write_mo(compiled, catalog)
    return folder
