"""The interface's catalogs: the template is current, and every language has every message (design section 7.2)."""

import re
from io import BytesIO
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "src" / "codetrail" / "locales"
PLACEHOLDER = re.compile(r"%\((\w+)\)s|\{(\w+)\}")
METHODS = [("src/codetrail/**.py", "python"), ("src/codetrail/web/templates/**.html", "jinja2")]
KEYWORDS = {"_": None, "gettext": None, "ngettext": (1, 2), "pgettext": ((1, "c"), 2)}


def message_key(message_id: object, context: str | None) -> tuple[object, str | None]:
    return (message_id, context)


def extracted() -> set[tuple[object, str | None]]:
    found = set()
    options = {"src/codetrail/web/templates/**.html": {"extensions": "jinja2.ext.i18n"}}
    for _file, _line, message, _comments, context in extract_from_dir(str(ROOT), METHODS, options, KEYWORDS):
        found.add(message_key(message if isinstance(message, str) else tuple(message), context))
    return found


def committed_template() -> Catalog:
    with (LOCALES / "codetrail.pot").open("rb") as handle:
        return read_po(handle)


def test_the_template_is_current() -> None:
    template = {
        message_key(m.id if isinstance(m.id, str) else tuple(m.id), m.context) for m in committed_template() if m.id
    }
    assert template == extracted(), "Run `just catalogs` and commit src/codetrail/locales/codetrail.pot"


def catalogs() -> list[Path]:
    return sorted(LOCALES.glob("*/LC_MESSAGES/codetrail.po"))


@pytest.mark.parametrize("path", catalogs(), ids=lambda path: path.parent.parent.name)
def test_every_language_has_every_message(path: Path) -> None:
    with path.open("rb") as handle:
        catalog = read_po(handle)
    template = committed_template()
    for message in template:
        if not message.id:
            continue
        translated = catalog.get(message.id, message.context)
        assert translated is not None and translated.string, f"{path}: missing {message.id!r}"
        originals = [str(text) for text in (message.id if isinstance(message.id, tuple | list) else (message.id,))]
        strings = [
            str(text)
            for text in (translated.string if isinstance(translated.string, tuple | list) else (translated.string,))
        ]
        expected = {match for original in originals for match in PLACEHOLDER.findall(original)}
        for string in strings:
            assert {match for match in PLACEHOLDER.findall(string)} <= expected, (
                f"{path}: placeholders in {message.id!r}"
            )
        if isinstance(message.id, tuple):
            assert len(strings) == catalog.num_plurals, f"{path}: plural forms of {message.id!r}"
    direction = catalog.get("ltr", "text direction")
    assert direction is not None and direction.string in ("ltr", "rtl")
    write_mo(BytesIO(), catalog)  # it compiles
