"""The interface's catalogs: the template is current, and every language has every message (design section 7.2)."""

import re
import subprocess
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


def unsafe_translations(catalog: Catalog) -> list[str]:
    """Translations that would break the page: markup the English lacks (Jinja trusts catalog text, also inside
    attributes), or text that doesn't format with the English message's values (Jinja formats every translation)."""
    found = []
    for message in catalog:
        if not message.id or not message.string:
            continue
        originals = [str(text) for text in (message.id if isinstance(message.id, tuple) else (message.id,))]
        strings = [str(text) for text in (message.string if isinstance(message.string, tuple) else (message.string,))]
        values = {name: "" for original in originals for name, _brace in PLACEHOLDER.findall(original) if name}
        for string in strings:
            added = [mark for mark in '<>"&' if mark in string and not any(mark in text for text in originals)]
            if added:
                found.append(f"{originals[0]!r} adds {''.join(added)}")
            try:
                string % values
            except KeyError, ValueError, TypeError:
                found.append(f"{originals[0]!r} doesn't format")
    return found


def test_unsafe_translations_are_found() -> None:
    catalog = Catalog(locale="ar")
    catalog.add("Passed", 'نجحت" style="x')
    catalog.add("%(count)s file", "%(total)s ملف")
    catalog.add("Home", "100% الرئيسية")
    catalog.add("Search", "بحث")
    assert unsafe_translations(catalog) == [
        "'Passed' adds \"",
        "'%(count)s file' doesn't format",
        "'Home' doesn't format",
    ]


@pytest.mark.parametrize("path", catalogs(), ids=lambda path: path.parent.parent.name)
def test_every_language_is_safe_in_the_page(path: Path) -> None:
    with path.open("rb") as handle:
        assert unsafe_translations(read_po(handle, locale=path.parent.parent.name)) == []


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


def test_compiled_catalogs_are_committed() -> None:
    # Codetrail reads only compiled catalogs, and an install builds from the committed tree (ADR 0013).
    compiled = "src/codetrail/locales/ar/LC_MESSAGES/codetrail.mo"
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", compiled], cwd=ROOT, check=False)
    assert ignored.returncode == 1, f"{compiled} is ignored by git"


@pytest.mark.parametrize("path", catalogs(), ids=lambda path: path.parent.parent.name)
def test_every_language_is_compiled_from_its_catalog(path: Path) -> None:
    with path.open("rb") as handle:
        catalog = read_po(handle, path.parent.parent.name)  # as `pybabel compile` reads it
    fuzzy = [str(message.id) for message in catalog if message.id and message.fuzzy]
    assert not catalog.fuzzy and fuzzy == [], f"{path}: `pybabel compile` skips what is marked fuzzy: {fuzzy}"
    compiled = BytesIO()
    write_mo(compiled, catalog)
    target = path.with_suffix(".mo")
    assert target.exists() and target.read_bytes() == compiled.getvalue(), (
        f"Run `just catalogs` and commit {target.relative_to(ROOT)}"
    )


def test_every_compiled_catalog_has_its_catalog() -> None:
    orphans = [path for path in LOCALES.glob("*/LC_MESSAGES/*.mo") if not path.with_suffix(".po").exists()]
    assert orphans == []
