"""The Repository page's chart data: what the script draws, with translated labels (design section 20.3)."""

import gettext
import json
from datetime import date

from codetrail.facts import Snapshot
from codetrail.guide import Page
from codetrail.metrics.activity import Activity
from codetrail.metrics.churn import AreaSpot, Churn, HeatTile
from codetrail.metrics.inventory import Change, Inventory
from codetrail.metrics.releases import Releases
from codetrail.metrics.repository import RepositoryReport
from codetrail.metrics.size import CodeSize, LanguageSize
from codetrail.repo.history import Tag
from codetrail.web.i18n import Language, installed_languages
from codetrail.web.repository_view import chart_document, plural_templates, script_json

ENGLISH = Language("en", "English", "ltr", gettext.NullTranslations())
HOSTILE = "</script><script>alert(1)</script><!-- & " + chr(0x2028)


def report() -> RepositoryReport:
    activity = Activity(
        5, 1, date(2026, 9, 21), date(2026, 9, 23), [], [], 1, 1, None, False, [3, 0, 2], date(2026, 9, 21)
    )
    releases = Releases([Tag("v1", "a" * 40, 1_790_000_000, HOSTILE)], 2, None)
    size = CodeSize(3, [LanguageSize("Python", 2, 10)], LanguageSize("Documents", 1, 1), 0, 0, 0, 10, 2, [])
    first = Snapshot(1, "b" * 40, "2026-09-20T10:00:00+00:00")
    later = Snapshot(2, "c" * 40, "2026-10-01T10:00:00+00:00")
    inventory = Inventory(first, [], [Change("package:pypi/httpx", later)], [Change("package:pypi/attrs", later)])
    area = Page("areas/app", {"kind": "area", "title": "The app"})
    churn = Churn(
        4, [], [], [AreaSpot(area, 3, 1, 4, 120)], [], 0, 2, 3,
        [HeatTile("app", "app", 120, 3)], [HeatTile("db.py", "app/db.py", 80, 2)],
        {"areas": 0, "folders": 0, "files": 5},
        ["2026-09", "2026-10"], [("areas/app", [None, 3])], [1, 2, 1, 0, 0, 0, 0],
    )  # fmt: skip
    return RepositoryReport(activity, None, releases, size, inventory, churn)


def test_english_plural_templates_have_one_and_other() -> None:
    assert plural_templates(
        ENGLISH, lambda n: ENGLISH.translations.ngettext("%(count)s commit", "%(count)s commits", n)
    ) == {
        "one": "{count} commit",
        "other": "{count} commits",
    }


def test_arabic_plural_templates_have_every_category() -> None:
    arabic = installed_languages()["ar"]
    templates = plural_templates(
        arabic, lambda n: arabic.translations.ngettext("%(count)s commit", "%(count)s commits", n)
    )
    assert set(templates) == {"zero", "one", "two", "few", "many", "other"}
    assert "{count}" in templates["few"] and templates["one"] != templates["few"]


def test_script_json_keeps_markup_inert() -> None:
    text = script_json({"note": HOSTILE})
    assert "<" not in text and ">" not in text and "&" not in text and chr(0x2028) not in text
    assert json.loads(text) == {"note": HOSTILE}


def test_the_document_holds_what_the_charts_draw() -> None:
    document = chart_document(report(), ENGLISH, date(2026, 10, 10))
    assert document["lang"] == "en"
    assert document["days"] == {"start": "2026-09-21", "counts": [3, 0, 2]}
    assert document["releases"] == [{"name": "v1", "date": "2026-09-21", "commit": "aaaaaaa", "notes": HOSTILE}]
    assert document["watchStart"] == "2026-09-20"
    assert document["deps"] == [
        {"name": "pypi/httpx", "sign": "+", "date": "2026-10-01"},
        {"name": "pypi/attrs", "sign": "-", "date": "2026-10-01"},
    ]
    heat = document["heat"]
    assert heat["areas"] == [{"name": "The app", "path": "areas/app", "href": "/pages/areas/app", "lines": 120,
                              "commits": 3, "documented": 1, "inferred": 4, "mostlyInferred": True,
                              "files": []}]  # fmt: skip
    assert heat["files"] == [{"name": "db.py", "path": "app/db.py", "href": "/source/app/db.py", "lines": 80,
                              "commits": 2}]  # fmt: skip
    assert heat["leftOut"] == {"areas": 0, "folders": 0, "files": 5}
    assert document["grid"] == {"months": ["2026-09", "2026-10"], "rows": [{"name": "The app", "cells": [None, 3]}]}
    labels = document["labels"]
    assert labels["months"][0] == "Jan" and len(labels["months"]) == 12
    assert labels["commits"] == {"one": "{count} commit", "other": "{count} commits"}
    assert labels["weekOf"] == "Week of {date}"


def test_a_report_without_history_has_no_days_or_heat() -> None:
    empty = RepositoryReport(None, None, None, report().size, Inventory(None, [], [], []), None)
    document = chart_document(empty, ENGLISH, date(2026, 10, 10))
    assert (document["days"], document["releases"], document["heat"], document["grid"]) == (None, None, None, None)
