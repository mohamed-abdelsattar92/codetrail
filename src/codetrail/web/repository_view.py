"""The Repository page's chart data: what `static/js/repository.js` draws, with its labels translated (design 20.3).

The document goes into the page as a JSON block the browser never runs, escaped so no text in it can end the block.
Counted phrases come once per plural category of the language, from Babel's plural rule; the script picks one with
`Intl.PluralRules`. Placeholders are written `{name}` for the script to fill.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from babel import Locale
from babel.dates import get_day_names, get_month_names

from codetrail.metrics.activity import Activity
from codetrail.metrics.churn import Churn, HotSpot
from codetrail.metrics.repository import RepositoryReport
from codetrail.metrics.size import LanguageSize
from codetrail.web.i18n import Language

PLACEHOLDER = re.compile(r"%\((\w+)\)s")
LISTED_FILES = 5  # files a tile's panel lists
# Characters that could end the JSON block or break a script, written as JSON's \u escapes instead.
ESCAPES = {character: f"\\u{ord(character):04x}" for character in ("<", ">", "&", chr(0x2028), chr(0x2029))}


def _template(text: str) -> str:
    return PLACEHOLDER.sub(r"{\1}", text)


def _locale(language: Language) -> Locale:
    return Locale.parse(language.code)


def plural_templates(language: Language, choose: Callable[[int], str]) -> dict[str, str]:
    """`choose(n)` is an ngettext call; one template per plural category, from the smallest number in it."""
    rule = _locale(language).plural_form
    templates: dict[str, str] = {}
    for number in range(200):
        templates.setdefault(rule(number), _template(choose(number)))
    return templates


def script_json(document: Mapping[str, object]) -> str:
    """The document as JSON safe inside `<script type="application/json">`: no `<`, `>` or `&` left to end it."""
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return "".join(ESCAPES.get(character, character) for character in text)


def _labels(language: Language) -> dict[str, object]:
    t = language.translations

    def plural(choose: Callable[[int], str]) -> dict[str, str]:
        return plural_templates(language, choose)

    return {
        "months": [str(name) for _, name in sorted(get_month_names("abbreviated", locale=_locale(language)).items())],
        "commits": plural(lambda n: t.ngettext("%(count)s commit", "%(count)s commits", n)),
        "lines": plural(lambda n: t.ngettext("%(count)s line", "%(count)s lines", n)),
        "commitsSince": plural(
            lambda n: t.ngettext("%(count)s commit since %(name)s", "%(count)s commits since %(name)s", n)
        ),
        "daysAfter": plural(lambda n: t.ngettext("%(count)s day after it", "%(count)s days after it", n)),
        "perMonth": t.gettext("Commits per month"),
        "perWeek": t.gettext("Commits per week"),
        "perDay": t.gettext("Commits per day"),
        "weekOf": _template(t.gettext("Week of %(date)s")),
        "window": _template(t.gettext("From %(start)s to %(end)s")),
        "firstRelease": t.gettext("The first release"),
        "arrived": _template(t.gettext("Arrived: %(name)s")),
        "left": _template(t.gettext("Left: %(name)s")),
        "watched": _template(t.gettext("Watched by Codetrail since %(date)s")),
        "noReleases": t.gettext("No releases in this window."),
        "noDeps": t.gettext("No dependency changes in this window."),
        "notRead": t.gettext("not read"),
        "area": t.gettext("Guide area"),
        "folder": t.gettext("Folder"),
        "file": t.gettext("File"),
        "mostlyInferred": t.gettext("mostly inferred rationale"),
        "openArea": t.gettext("Open the area page"),
        "openFile": t.gettext("Open the file"),
        "noFiles": t.gettext("No file in it changed in the commits read."),
        "releases": t.gettext("Releases"),
        "dependencies": t.gettext("Dependencies"),
        "wholeHistory": t.gettext("Whole history"),
        "plotHelp": t.gettext("Use the left and right arrow keys to read each bar."),
        "calendarHelp": t.gettext(
            "Commits per day over the last 53 weeks; the numbers by month are in the timeline's table."
        ),
        "noAreas": t.gettext("Areas are counted once your assistant has written the guide's area pages."),
        "weekdays": [str(name) for _, name in sorted(get_day_names("abbreviated", locale=_locale(language)).items())],
    }  # fmt: skip


def _day(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).date().isoformat()


def _top(files: list[HotSpot], keep: Callable[[str], bool]) -> list[list[object]]:
    return [[spot.name, spot.count] for spot in files if keep(spot.name)][:LISTED_FILES]


def _heat(churn: Churn) -> dict[str, object]:
    def in_folder(folder: str) -> Callable[[str], bool]:
        return lambda path: "/" not in path if folder == "." else path.startswith(folder + "/")

    def in_scope(scope: tuple[str, ...]) -> Callable[[str], bool]:
        prefixes = [part.strip("/") for part in scope]
        return lambda path: any(not part or path == part or path.startswith(part + "/") for part in prefixes)

    areas = [
        {"name": spot.page.title, "path": spot.page.id, "href": "/pages/" + quote(spot.page.id), "lines": spot.lines,
         "commits": spot.commits, "documented": spot.documented, "inferred": spot.inferred,
         "mostlyInferred": spot.inferred > spot.documented * 2, "files": _top(churn.files, in_scope(spot.scope))}
        for spot in churn.areas
    ]  # fmt: skip
    folders = [{"name": tile.name, "path": tile.path, "href": None, "lines": tile.lines, "commits": tile.commits,
                "files": _top(churn.files, in_folder(tile.path))} for tile in churn.folder_tiles]  # fmt: skip
    files = [{"name": tile.name, "path": tile.path, "href": "/source/" + quote(tile.path), "lines": tile.lines,
              "commits": tile.commits} for tile in churn.file_tiles]  # fmt: skip
    return {"areas": areas, "folders": folders, "files": files, "leftOut": dict(churn.left_out)}


def chart_document(report: RepositoryReport, language: Language, today: date) -> dict[str, Any]:
    """Everything the page's script draws: the timeline, the calendar, the heat map and the area-by-month grid."""
    activity, releases, churn, inventory = report.activity, report.releases, report.churn, report.inventory
    days = None
    if activity is not None and activity.days_start is not None:
        days = {"start": activity.days_start.isoformat(), "counts": list(activity.days)}
    release_list = None
    if releases is not None:
        release_list = [{"name": tag.name, "date": _day(tag.date), "commit": tag.commit[:7], "notes": tag.message}
                        for tag in sorted(releases.tags, key=lambda tag: (tag.date, tag.name))]  # fmt: skip
    deps = [{"name": change.package.split(":", 1)[-1], "sign": sign, "date": change.snapshot.taken_at[:10]}
            for sign, changes in (("+", inventory.arrived), ("-", inventory.left)) for change in changes]  # fmt: skip
    grid = None
    if churn is not None:
        titles = {spot.page.id: spot.page.title for spot in churn.areas}
        rows = [{"name": titles.get(page_id, page_id), "cells": list(cells)} for page_id, cells in churn.area_months]
        grid = {"months": list(churn.months), "rows": rows}
    return {
        "lang": language.code,
        "today": today.isoformat(),
        "days": days,
        "releases": release_list,
        "watchStart": inventory.since.taken_at[:10] if inventory.since else None,
        "deps": deps,
        "heat": _heat(churn) if churn is not None else None,
        "grid": grid,
        "labels": _labels(language),
    }


def months_table(activity: Activity | None) -> list[tuple[str, int]]:
    """Commits per month ("YYYY-MM"), oldest first, from the commits per day."""
    if activity is None or activity.days_start is None:
        return []
    months: dict[str, int] = {}
    for offset, count in enumerate(activity.days):
        key = (activity.days_start + timedelta(days=offset)).strftime("%Y-%m")
        months[key] = months.get(key, 0) + count
    return list(months.items())


def bar_heights(values: Sequence[float]) -> list[int]:
    """Each value as a percent of the highest, for the bars the page draws on the server."""
    highest = max(values, default=0)
    return [round(value / highest * 100) if highest else 0 for value in values]


def ramp_step(count: int, highest: int) -> int:
    """The step of the five-step ramp a count falls in: 0 for nothing, then quarters of the highest."""
    if count <= 0 or highest <= 0:
        return 0
    return min(4, 1 + (count * 4 - 1) // highest)


def language_segments(languages: Sequence[LanguageSize], keep: int = 4) -> list[tuple[str, int, int]]:
    """The largest languages by lines, each with its color slot, then the rest as Other in the last slot."""
    segments = [(size.language, size.lines, slot) for slot, size in enumerate(languages[:keep], start=1)]
    rest = sum(size.lines for size in languages[keep:])
    return [*segments, ("Other", rest, keep + 1)] if rest else segments


def busiest_weekday(activity: Activity | None) -> int | None:
    """The weekday (0 for Monday) with the most commits, or None without any."""
    if activity is None or activity.days_start is None or not any(activity.days):
        return None
    totals = [0] * 7
    for offset, count in enumerate(activity.days):
        totals[(activity.days_start + timedelta(days=offset)).weekday()] += count
    return totals.index(max(totals))
