"""What the page's shell shows around every page: the sidebar, path neighbours and where to continue (16.1)."""

from codetrail.guide import Page
from codetrail.web.navigation import answers_newest_first, build_navigation, continue_reading, path_neighbours


def page(page_id: str, kind: str, title: object = "", **meta: object) -> Page:
    return Page(page_id, {"kind": kind, "title": title or page_id, **meta}, "Body.")


PAGES = [
    page("areas/web", "area", "Web"),
    page("areas/api", "area", "API"),
    page("concepts/retry", "concept", "Retries"),
    page("concepts/auth", "concept", "Auth"),
    page("paths/start", "path", "Start here", steps=["areas/api", "concepts/retry", "areas/web"]),
    page("paths/deep", "path", "Deep dive", steps=["concepts/auth", "concepts/retry"]),
    page("digests/one", "digest", "One", written_at="2026-10-01"),
    page("answers/2026-10-01-a-111111", "answer", "Older?", asked_at="2026-10-01T10:00:00+00:00"),
    page("answers/2026-10-05-b-222222", "answer", "Newer?", asked_at="2026-10-05T10:00:00+00:00"),
    page("answers/2026-10-03-c-333333", "answer", ["odd", "title"]),
]


def test_the_sidebar_lists_paths_with_progress_areas_and_concepts_by_title() -> None:
    statuses = {"areas/api": "learned", "concepts/retry": "stale"}
    navigation = build_navigation(PAGES, statuses, unread_digests=["digests/one"])
    assert [(path.id, learned, total) for path, learned, total in navigation.paths] == [
        ("paths/deep", 0, 2),
        ("paths/start", 1, 3),
    ]
    assert [area.id for area in navigation.areas] == ["areas/api", "areas/web"]
    assert [concept.id for concept in navigation.concepts] == ["concepts/auth", "concepts/retry"]
    assert navigation.statuses == statuses
    assert navigation.unread_digests is True


def test_the_sidebar_shows_the_newest_five_answers_and_counts_them_all() -> None:
    answers = [page(f"answers/2026-10-0{day}-q-00000{day}", "answer", f"Q{day}", asked_at=f"2026-10-0{day}")
               for day in range(1, 8)]  # fmt: skip
    navigation = build_navigation(answers, {}, unread_digests=[])
    assert [answer.title for answer in navigation.answers] == ["Q7", "Q6", "Q5", "Q4", "Q3"]
    assert navigation.answer_count == 7


def test_answers_with_a_malformed_date_sort_last() -> None:
    ordered = answers_newest_first(p for p in PAGES if p.kind == "answer")  # a generator, read once
    assert [answer.id for answer in ordered] == [
        "answers/2026-10-05-b-222222",
        "answers/2026-10-01-a-111111",
        "answers/2026-10-03-c-333333",
    ]


def test_an_empty_guide_gives_an_empty_navigation() -> None:
    navigation = build_navigation([], {}, unread_digests=[])
    assert (navigation.paths, navigation.areas, navigation.answers, navigation.answer_count) == ([], [], [], 0)


def test_neighbours_follow_the_path_the_reader_came_from() -> None:
    path, previous, following = path_neighbours(PAGES, "concepts/retry", "paths/deep")
    assert (path.id if path else None, previous.id if previous else None, following) == (
        "paths/deep", "concepts/auth", None,
    )  # fmt: skip


def test_an_unknown_or_unrelated_path_falls_back_to_the_first_path_holding_the_page() -> None:
    for hint in (None, "paths/missing", "../x", "paths/deep-but-not", "areas/web"):
        path, previous, following = path_neighbours(PAGES, "areas/web", hint)
        assert path is not None and path.id == "paths/start"
        assert previous is not None and previous.id == "concepts/retry"
        assert following is None


def test_a_page_in_no_path_has_no_neighbours() -> None:
    assert path_neighbours(PAGES, "areas/missing", None) == (None, None, None)


def test_continue_reading_picks_the_first_unlearned_step() -> None:
    found = continue_reading(PAGES, {"concepts/auth": "learned"})
    assert found is not None
    path, step, learned, total = found
    assert (path.id, step.id, learned, total) == ("paths/deep", "concepts/retry", 1, 2)


def test_continue_reading_is_none_when_every_path_is_learned() -> None:
    learned = {step: "learned" for step in ("areas/api", "concepts/retry", "areas/web", "concepts/auth")}
    assert continue_reading(PAGES, learned) is None
