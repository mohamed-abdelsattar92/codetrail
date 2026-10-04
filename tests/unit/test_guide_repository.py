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
