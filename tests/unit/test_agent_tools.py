"""The four tools coding agents call: map, brief, page and changes (design sections 23.5 and 23.7)."""

from dataclasses import replace
from pathlib import Path

import pytest

from codetrail.agents.export import AgentExport, ExportedPage, write_export
from codetrail.agents.tools import TOOLS, AgentTools, ToolInputError
from codetrail.config import AgentSettings, Paths, write_target
from codetrail.lock import target_lock, target_removal
from tests.fixtures.repos import make_repository

COMMITS = ["1" * 40, "2" * 40, "3" * 40]


def area(page_id: str, title: str, scope: str, brief: str, stale: bool = False) -> ExportedPage:
    return ExportedPage(page_id, "area", title, [scope], COMMITS[2], stale, f"# Brief: {title}\n\n{brief}\n",
                        f"# {title} ({page_id})\n\nThe whole {title} page.\n")  # fmt: skip


def digest(number: int, title: str) -> ExportedPage:
    page_id = f"digests/2026-10-0{number + 1}-{COMMITS[number][:12]}"
    return ExportedPage(page_id, "digest", title, [], COMMITS[number], False, "", f"{title} digest text.\n",
                        f"2026-10-0{number + 1}T10:00:00+00:00", COMMITS[number - 1])  # fmt: skip


PAGES = [
    area("areas/api", "The API", "services/api", "API brief."),
    area("areas/app", "The app", "services/api/app", "Possibly stale: read the code.\n\nApp brief.", stale=True),
    replace(area("concepts/contract", "The contract", "services/api/app/main.py", "Contract brief."), kind="concept"),
    digest(2, "Third"),
    digest(1, "Second"),
]
EXPORT = AgentExport("t", "/work/t", "develop", COMMITS[2], "2026-10-03T10:00:00+00:00", "# Map of t\n\nThe map.\n",
                     PAGES, COMMITS)  # fmt: skip


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [{"README.md": "# T\n"}]), "develop")
    return paths


def tools_for(paths: Paths, export: AgentExport | None = EXPORT, **settings: int) -> AgentTools:
    if export is not None:
        write_export(paths.target_data("t") / "agents", export)
    return AgentTools(paths, "t", AgentSettings(**settings))


@pytest.fixture
def tools(paths: Paths) -> AgentTools:
    return tools_for(paths)


def test_the_tools_are_listed_with_strict_schemas() -> None:
    assert [tool["name"] for tool in TOOLS] == [
        "codetrail_map",
        "codetrail_brief",
        "codetrail_page",
        "codetrail_changes",
    ]
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in TOOLS)  # type: ignore[index]


def test_map_returns_the_exported_map(tools: AgentTools) -> None:
    answer = tools.call("codetrail_map", {})
    assert answer.startswith("# Map of t\n\nThe map.") and answer.rstrip().endswith(f"Describes {COMMITS[2][:12]}.")


def test_brief_by_page_and_by_path(tools: AgentTools) -> None:
    assert "API brief." in tools.call("codetrail_brief", {"page": "areas/api"})
    answer = tools.call("codetrail_brief", {"path": "services/api/app/main.py"})
    assert "API brief." not in answer  # only the innermost area
    assert answer.index("App brief.") < answer.index("Contract brief.")


def test_brief_paths_are_normalised(tools: AgentTools) -> None:
    answers = {tools.call("codetrail_brief", {"path": path}) for path in ("./services/api/", "services/api")}
    assert len(answers) == 1 and "API brief." in answers.pop()
    assert "App brief." in tools.call("codetrail_brief", {"path": "./services/api/app/"})


def test_brief_caps_how_many_it_returns(paths: Paths) -> None:
    answer = tools_for(paths, max_briefs_per_call=1).call("codetrail_brief", {"path": "services/api/app/main.py"})
    assert "App brief." in answer and "Contract brief." not in answer


def test_brief_with_no_pages_says_so(paths: Paths) -> None:
    answer = tools_for(paths, replace(EXPORT, pages=[])).call("codetrail_brief", {"path": "services/api"})
    assert answer.startswith("No page covers services/api;")
    with pytest.raises(ToolInputError):
        tools_for(paths, replace(EXPORT, pages=[])).call("codetrail_page", {"page": "areas/api"})


def test_brief_marks_stale_pages(tools: AgentTools) -> None:
    assert "Possibly stale" in tools.call("codetrail_brief", {"page": "areas/app"})


def test_page_returns_the_converted_page(tools: AgentTools) -> None:
    answer = tools.call("codetrail_page", {"page": "areas/api"})
    assert "The whole The API page." in answer and "---" not in answer


def test_changes_since_a_commit_a_prefix_an_unknown_commit_and_a_date(tools: AgentTools) -> None:
    since_second = tools.call("codetrail_changes", {"since": COMMITS[1]})
    assert "Third digest text." in since_second and "Second digest text." not in since_second
    since_first = tools.call("codetrail_changes", {"since": COMMITS[0][:7]})
    assert since_first.index("Third digest text.") < since_first.index("Second digest text.")
    unknown = tools.call("codetrail_changes", {"since": "abcdef1"})
    assert unknown.startswith("abcdef1 isn't a commit this guide knows") and "Third digest text." in unknown
    by_date = tools.call("codetrail_changes", {"since": "2026-10-03"})
    assert "Third digest text." in by_date and "Second digest text." not in by_date


def test_an_ambiguous_prefix_is_refused(paths: Paths) -> None:
    export = replace(EXPORT, snapshots=["abcdef1" + "a" * 33, "abcdef1" + "b" * 33])
    with pytest.raises(ToolInputError, match="matches several commits"):
        tools_for(paths, export).call("codetrail_changes", {"since": "abcdef1"})


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("codetrail_page", {"page": "../etc/passwd"}),
        ("codetrail_page", {"page": "areas/missing"}),
        ("codetrail_page", {"page": 3}),
        ("codetrail_brief", {"path": "/etc/passwd"}),
        ("codetrail_brief", {"path": "a/../b"}),
        ("codetrail_brief", {"path": "a\x00b"}),
        ("codetrail_brief", {"path": "a\nb"}),
        ("codetrail_brief", {"path": "a" * 1001}),
        ("codetrail_brief", {"path": "a", "page": "areas/api"}),
        ("codetrail_brief", {}),
        ("codetrail_changes", {"since": "abc"}),
        ("codetrail_changes", {"since": "ABCDEF12"}),
        ("codetrail_changes", {"since": "2026-13-40"}),
        ("codetrail_map", {"extra": 1}),
    ],
)
def test_inputs_are_refused(tools: AgentTools, tool: str, arguments: dict[str, object]) -> None:
    with pytest.raises(ToolInputError):
        tools.call(tool, arguments)


def test_an_unknown_tool_is_a_key_error(tools: AgentTools) -> None:
    with pytest.raises(KeyError):
        tools.call("codetrail_write", {})


def test_answers_are_cut_to_the_response_limit(paths: Paths) -> None:
    long = replace(PAGES[0], text="word " * 2000)
    answer = tools_for(paths, replace(EXPORT, pages=[long]), max_response_words=500).call(
        "codetrail_page", {"page": "areas/api"}
    )
    assert len(answer.split()) <= 520 and "cut at 500 words" in answer


def test_a_call_during_and_after_removal(tools: AgentTools, paths: Paths) -> None:
    with target_removal(paths, "t"):
        assert tools.call("codetrail_map", {}) == "Codetrail no longer has the target 't'."
    paths.target_file("t").unlink()
    assert tools.call("codetrail_map", {}) == "Codetrail no longer has the target 't'."


def test_no_call_takes_the_update_lock(tools: AgentTools, paths: Paths) -> None:
    with target_lock(paths, "t"):
        calls: list[tuple[str, dict[str, object]]] = [
            ("codetrail_map", {}),
            ("codetrail_brief", {"page": "areas/api"}),
            ("codetrail_page", {"page": "areas/api"}),
            ("codetrail_changes", {"since": "2026-10-01"}),
        ]
        for tool, arguments in calls:
            assert "Describes" in tools.call(tool, arguments)


def test_a_target_without_an_export_says_what_to_run(paths: Paths) -> None:
    answer = tools_for(paths, None).call("codetrail_map", {})
    assert answer == "Codetrail has no context for t yet: run codetrail update t or codetrail agents t."
