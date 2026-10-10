"""The Repository page's chart data: what the script draws, with translated labels (design section 20.3)."""

import gettext
import json
import subprocess
import sys
from datetime import date

from codetrail.facts import Snapshot
from codetrail.guide import Page
from codetrail.metrics.activity import Activity
from codetrail.metrics.churn import AreaSpot, Churn, HeatTile, HotSpot
from codetrail.metrics.inventory import Change, Inventory
from codetrail.metrics.releases import Releases
from codetrail.metrics.repository import RepositoryReport
from codetrail.metrics.size import CodeSize, LanguageSize
from codetrail.repo.history import Tag
from codetrail.web.i18n import Language, installed_languages
from codetrail.web.repository_view import (
    bar_heights,
    busiest_weekday,
    chart_document,
    language_segments,
    months_table,
    plural_templates,
    ramp_step,
    script_json,
)

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


def test_plural_templates_come_from_the_catalog_for_sample_numbers() -> None:
    choose = lambda n: ENGLISH.translations.ngettext("%(count)s commit", "%(count)s commits", n)  # noqa: E731
    templates = plural_templates(choose)
    assert templates["1"] == "{count} commit" and templates["0"] == templates["2"] == "{count} commits"
    assert {"0", "1", "2", "3", "11", "100"} <= set(templates)


def test_arabic_plural_templates_follow_the_catalog_s_own_rule() -> None:
    arabic = installed_languages()["ar"]
    templates = plural_templates(lambda n: arabic.translations.ngettext("%(count)s line", "%(count)s lines", n))
    assert templates["1"] != templates["3"] != templates["11"]  # one, few and many forms
    assert "{count}" in templates["3"]


def test_the_page_runs_without_babel_installed() -> None:
    script = "import sys; sys.modules['babel'] = None; import codetrail.server, codetrail.web.repository_view"
    subprocess.run([sys.executable, "-c", script], check=True)


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
    assert labels["months"][0] == "Jan" and len(labels["months"]) == 12  # from the catalog
    assert labels["commits"]["1"] == "{count} commit" and labels["commits"]["2"] == "{count} commits"
    assert labels["weekOf"] == "Week of {date}"
    assert labels["weekdays"][:2] == ["Mon", "Tue"] and len(labels["weekdays"]) == 7
    for key in ("releases", "dependencies", "wholeHistory", "plotHelp", "calendarHelp", "noAreas"):
        assert labels[key], key


def test_a_report_without_history_has_no_days_or_heat() -> None:
    empty = RepositoryReport(None, None, None, report().size, Inventory(None, [], [], []), None)
    document = chart_document(empty, ENGLISH, date(2026, 10, 10))
    assert (document["days"], document["releases"], document["heat"], document["grid"]) == (None, None, None, None)


def test_months_are_summed_from_the_days() -> None:
    activity = report().activity
    assert months_table(activity) == [("2026-09", 5)]
    later = Activity(
        1, 0, date(2026, 9, 30), date(2026, 9, 30), [], [], 1, 1, None, False, [1, 0, 2], date(2026, 9, 30)
    )
    assert months_table(later) == [("2026-09", 1), ("2026-10", 2)]
    assert months_table(None) == []


def test_bars_scale_to_the_highest_value() -> None:
    assert bar_heights([0, 5, 10]) == [0, 50, 100]
    assert bar_heights([0, 0]) == [0, 0]
    assert bar_heights([]) == []


def test_ramp_steps_split_the_range_into_five() -> None:
    assert [ramp_step(count, 100) for count in (0, 1, 25, 26, 50, 75, 76, 100)] == [0, 1, 1, 2, 2, 3, 4, 4]
    assert ramp_step(0, 0) == 0


def test_languages_keep_four_then_other() -> None:
    from codetrail.metrics.size import LanguageSize

    sizes = [
        LanguageSize(name, 1, lines) for name, lines in (("A", 50), ("B", 40), ("C", 30), ("D", 20), ("E", 5), ("F", 5))
    ]
    assert language_segments(sizes) == [("A", 50, 1, 33), ("B", 40, 2, 27), ("C", 30, 3, 20), ("D", 20, 4, 13),
                                        ("Other", 10, 5, 7)]  # fmt: skip
    assert language_segments(sizes[:2]) == [("A", 50, 1, 56), ("B", 40, 2, 44)]
    assert language_segments([LanguageSize("Python", 2, 0)]) == [("Python", 0, 1, 0)]  # empty files: no division


def test_the_busiest_weekday_counts_commits_by_weekday() -> None:
    assert busiest_weekday(report().activity) == 0  # 2026-09-21 is a Monday, with 3 of the 5
    assert busiest_weekday(None) is None


def test_a_folder_lists_only_its_own_files_and_empty_tiles_are_counted_not_drawn() -> None:
    area = Page("areas/docs", {"kind": "area", "title": "The docs"})
    churn = Churn(
        2, [HotSpot("src/a.py", 3), HotSpot("src/codetrail/b.py", 5)], [], [AreaSpot(area, 4, 0, 1, 0)], [], 0, 1, 1,
        [HeatTile("src", "src", 10, 3), HeatTile("src/codetrail", "src/codetrail", 20, 5),
         HeatTile("empty", "empty", 0, 0)],
        [], {"areas": 0, "folders": 0, "files": 0}, [], [], [], folder_depth=2, tile_cap=60,
    )  # fmt: skip
    plain = report()
    with_churn = RepositoryReport(plain.activity, None, plain.releases, plain.size, plain.inventory, churn)
    document = chart_document(with_churn, ENGLISH, date(2026, 10, 10))
    heat = document["heat"]
    assert [tile["files"] for tile in heat["folders"]] == [[["src/a.py", 3]], [["src/codetrail/b.py", 5]]]
    assert heat["areas"] == []  # an area with no lines of code has no size to draw
    assert heat["leftOut"] == {"areas": 1, "folders": 1, "files": 0}


def test_areas_are_capped_like_the_other_levels() -> None:
    areas = [AreaSpot(Page(f"areas/{n}", {"kind": "area", "title": str(n)}), 1, 0, 0, 10 + n) for n in range(3)]
    churn = Churn(1, [], [], areas, [], 0, 1, 1, [], [], {"areas": 1, "folders": 0, "files": 0}, [], [], [],
                  folder_depth=2, tile_cap=2)  # fmt: skip
    plain = report()
    with_churn = RepositoryReport(plain.activity, None, plain.releases, plain.size, plain.inventory, churn)
    document = chart_document(with_churn, ENGLISH, date(2026, 10, 10))
    assert [tile["name"] for tile in document["heat"]["areas"]] == ["2", "1"]
    assert document["heat"]["leftOut"]["areas"] == 1
    assert document["labels"]["noCode"]
