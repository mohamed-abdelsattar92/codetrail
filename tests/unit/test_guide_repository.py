"""The guide: Markdown pages with YAML front matter in their own git repository (design section 6.1)."""

from pathlib import Path

import pytest

from codetrail.errors import CodetrailError
from codetrail.guide import GuideRepository, Page


@pytest.fixture
def guide(tmp_path: Path) -> GuideRepository:
    repository = GuideRepository(tmp_path / "guide")
    repository.ensure()
    return repository


def page(page_id: str = "concepts/contract-first", body: str = "# Body\n\nText.") -> Page:
    return Page(
        page_id,
        {"kind": "concept", "title": "Contract first", "facts": [{"id": "decision:ADR-0007", "hash": "a"}]},
        body,
    )


def test_pages_round_trip(guide: GuideRepository) -> None:
    guide.write_page(page())
    assert guide.read_page("concepts/contract-first") == page()
    assert [found.id for found in guide.pages()] == ["concepts/contract-first"]
    assert guide.pages("area") == []


@pytest.mark.parametrize(
    "page_id", ["../escape", "concepts/../x", "concepts/UPPER", "other/x", "concepts/", "concepts/a/b"]
)
def test_unsafe_ids_are_refused(guide: GuideRepository, page_id: str) -> None:
    with pytest.raises(CodetrailError):
        guide.write_page(page(page_id))


def test_commit_and_discard(guide: GuideRepository) -> None:
    guide.write_page(page())
    assert guide.has_uncommitted_changes()
    assert guide.commit("First pages") is not None
    assert not guide.has_uncommitted_changes()
    assert guide.commit("Nothing new") is None
    guide.write_page(page(body="Changed"))
    guide.write_page(page("concepts/stray"))
    guide.discard()
    assert guide.read_page("concepts/contract-first") == page()
    assert guide.read_page("concepts/stray") is None
    assert not guide.has_uncommitted_changes()


def test_the_outline_round_trips(guide: GuideRepository) -> None:
    outline = {"pages": [{"id": "areas/api", "kind": "area", "title": "The API", "scope_paths": ["services/api"]}]}
    guide.write_outline(outline)
    assert guide.read_outline() == outline


def test_front_matter_with_hostile_text_stays_data(guide: GuideRepository) -> None:
    hostile = Page("concepts/x", {"kind": "concept", "title": "---\n!!python/object:os.system 'id'"}, "---\nbody")
    guide.write_page(hostile)
    assert guide.read_page("concepts/x") == hostile


@pytest.mark.parametrize("commit", ["--output=/tmp/x", "-p", "HEAD", "not-hex", "a" * 41])
def test_file_at_refuses_anything_but_a_commit_id(guide: GuideRepository, commit: str) -> None:
    guide.write_page(page())
    guide.commit("First")
    assert guide.file_at(commit, "concepts/contract-first") is None


def test_file_at_reads_a_page_at_a_commit(guide: GuideRepository) -> None:
    guide.write_page(page())
    head = guide.commit("First")
    assert head is not None
    assert "# Body" in (guide.file_at(head, "concepts/contract-first") or "")


def test_checking_for_uncommitted_changes_never_takes_the_index_lock(
    guide: GuideRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The page checks while an update may be committing; an optional index refresh would hold index.lock (16.3).
    from codetrail import guide as guide_module

    calls: list[list[str]] = []
    real = guide_module.run_git  # type: ignore[attr-defined]

    def recording(arguments: list[str], **options: object) -> bytes:
        calls.append(arguments)
        return real(arguments, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(guide_module, "run_git", recording)
    assert guide.has_uncommitted_changes() is False
    [arguments] = calls
    assert arguments.index("--no-optional-locks") < arguments.index("status")


def test_has_guide_pages_stops_at_the_first_area_or_concept(tmp_path: Path) -> None:
    guide = GuideRepository(tmp_path / "guide")
    assert not guide.has_guide_pages()  # no folder yet
    guide.write_page(Page("paths/start", {"kind": "path", "title": "Start"}, "Steps."))
    assert not guide.has_guide_pages()  # a path alone teaches nothing

    guide.write_page(Page("areas/app", {"kind": "area", "title": "The app"}, "How it works."))
    (guide.root / "concepts").mkdir()
    (guide.root / "concepts" / "broken.md").write_bytes(b"---\n\xff\n---\n")  # read after the area: never parsed

    assert guide.has_guide_pages()


@pytest.mark.parametrize("end", ["--output=/tmp/x", "HEAD", "c" * 41, None])
def test_the_covered_commit_is_the_newest_digest_s_end(guide: GuideRepository, end: str | None) -> None:
    assert guide.covered_commit() is None  # no paid update has written the guide yet
    for day, to_commit in (("2026-11-10", "a" * 40), ("2026-11-12", end), ("2026-11-11", "b" * 40)):
        meta = {"kind": "digest", "title": "D", "to_commit": to_commit, "written_at": f"{day}T10:00:00+00:00"}
        guide.write_page(Page(f"digests/{day}", meta, "Things changed."))
    assert guide.covered_commit() is None  # the newest end isn't a commit id: nothing, never an option for git
    guide.write_page(Page("digests/2026-11-13", {"kind": "digest", "title": "D", "to_commit": "c" * 40,
                                                  "written_at": "2026-11-13T10:00:00+00:00"}, "More."))  # fmt: skip
    assert guide.covered_commit() == "c" * 40
