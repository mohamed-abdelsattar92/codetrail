"""Interface languages: one gettext catalog per language, English first and as the fallback (design section 7.2).

A catalog names itself and its writing direction by translating two messages with a context: "English" in the
context "language name", and "ltr" in the context "text direction" (as `ltr` or `rtl`). The installed languages are
English plus every compiled catalog found.
"""

from __future__ import annotations

import gettext
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

LANGUAGE_CODE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")
DOMAIN = "codetrail"


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    direction: str
    translations: gettext.NullTranslations


def default_locales() -> Path:
    return Path(str(files("codetrail").joinpath("locales")))


def installed_languages(locales: Path | None = None) -> dict[str, Language]:
    languages = {"en": Language("en", "English", "ltr", gettext.NullTranslations())}
    folder = locales or default_locales()
    for catalog in sorted(folder.glob(f"*/LC_MESSAGES/{DOMAIN}.mo")):
        code = catalog.parent.parent.name
        if not LANGUAGE_CODE.fullmatch(code) or code == "en":
            continue
        with catalog.open("rb") as handle:
            translations = gettext.GNUTranslations(handle)
        direction = translations.pgettext("text direction", "ltr")
        name = translations.pgettext("language name", "English")
        languages[code] = Language(code, name, direction if direction in ("ltr", "rtl") else "ltr", translations)
    return languages
