"""The outline and scopes: what each page covers, and when a page must be rewritten (design sections 4.3, 6.2)."""

from pathlib import Path

import pytest

from codetrail.config import ToolsSettings
from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore
from codetrail.generate.outline import OutlineEntry, uncovered_facts, validate_outline, validate_paths
from codetrail.generate.scope import affected_reason, facts_in_scope, page_meta, scope_hash, sources_changed
from codetrail.guide import Page
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest
from tests.fixtures.repos import fake_github_token


def module(path: str, name: str) -> Entity:
    return Entity(f"module:{path}", EntityKind.MODULE, {"name": name}, (Source(path),))


ADR = Entity("decision:ADR-0007", EntityKind.DECISION, {"title": "REST"}, (Source("docs/adr/0007-rest.md"),))
FILES = {"services/api/app/a.py": "1" * 40, "services/api/app/b.py": "2" * 40, "docs/adr/0007-rest.md": "3" * 40}


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    store = FactStore(connect(tmp_path / "codetrail.db"))
    store.record(
        "c1",
        [module("services/api/app/a.py", "app.a"), module("services/api/app/b.py", "app.b"), ADR],
        [Relation("module:services/api/app/a.py", RelationKind.IMPORTS, "module:services/api/app/b.py")],
    )
    return store


@pytest.fixture
def manifest() -> SourceManifest:
    return SourceManifest("c1", FILES)


@pytest.fixture
def scanner() -> SecretScanner:
    return SecretScanner(ToolsSettings())


def entry(**overrides: object) -> OutlineEntry:
    values: dict[str, object] = {
        "id": "areas/api", "kind": "area", "title": "The API", "scope_paths": ["services/api"],
        "scope_kinds": [], "facts": ["decision:ADR-0007"],
    }  # fmt: skip
    values.update(overrides)
    return OutlineEntry(**values)  # type: ignore[arg-type]


def test_valid_entries_pass_and_bad_ones_are_dropped_with_reasons(
    store: FactStore, manifest: SourceManifest, scanner: SecretScanner
) -> None:
    raw = [
        {
            "id": "areas/api",
            "kind": "area",
            "title": "The API",
            "scope_paths": ["services/api"],
            "facts": ["decision:ADR-0007", "module:ghost.py"],
        },
        {"id": "areas/api", "kind": "area", "title": "Duplicate", "scope_paths": ["services/api"], "facts": []},
        {"id": "concepts/Bad Id", "kind": "concept", "title": "x", "scope_paths": ["services"], "facts": []},
        {"id": "concepts/nowhere", "kind": "concept", "title": "x", "scope_paths": ["missing/folder"], "facts": []},
        {"id": "areas/concept-kind", "kind": "concept", "title": "x", "scope_paths": ["services"], "facts": []},
    ]
    entries, problems = validate_outline(raw, store, manifest, scanner)
    assert [entry.id for entry in entries] == ["areas/api"]
    assert entries[0].facts == ["decision:ADR-0007"]
    assert len(problems) == 5


def test_facts_in_scope_follow_paths_kinds_and_named_facts(store: FactStore) -> None:
    found = {entity.id for entity in facts_in_scope(store, entry())}
    assert found == {"module:services/api/app/a.py", "module:services/api/app/b.py", "decision:ADR-0007"}
    only_decisions = {entity.id for entity in facts_in_scope(store, entry(scope_kinds=["decision"], facts=[]))}
    assert only_decisions == set()


def test_a_page_is_affected_when_new_or_when_its_facts_change(
    store: FactStore, manifest: SourceManifest, tmp_path: Path
) -> None:
    assert affected_reason(entry(), None, store) == "new"
    page = Page("areas/api", page_meta(entry(), store, manifest, ["services/api/app/a.py"]), "Body")
    assert affected_reason(entry(), page, store) is None
    store.record(
        "c2", [module("services/api/app/a.py", "app.alpha"), module("services/api/app/b.py", "app.b"), ADR], []
    )
    assert affected_reason(entry(), page, store) == "scope changed"
    changed_adr = Entity(ADR.id, ADR.kind, {"title": "REST, revised"}, ADR.sources)
    page = Page("areas/api", page_meta(entry(), store, manifest, []), "Body")
    store.record(
        "c3", [module("services/api/app/a.py", "app.alpha"), module("services/api/app/b.py", "app.b"), changed_adr], []
    )
    assert affected_reason(entry(), page, store) == "facts changed"


def test_a_retitled_entry_rewrites_its_page(store: FactStore, manifest: SourceManifest) -> None:
    page = Page("areas/api", page_meta(entry(), store, manifest, []), "Body")
    assert affected_reason(entry(title="The API service"), page, store) == "outline changed"


def test_sources_changed_compares_the_blobs_read(store: FactStore, manifest: SourceManifest) -> None:
    page = Page("areas/api", page_meta(entry(), store, manifest, ["services/api/app/a.py"]), "Body")
    assert not sources_changed(page, manifest)
    assert sources_changed(page, SourceManifest("c2", {**FILES, "services/api/app/a.py": "9" * 40}))


def test_scope_hash_is_stable(store: FactStore) -> None:
    assert scope_hash(store, entry()) == scope_hash(store, entry())


def test_uncovered_facts_are_those_no_page_covers(store: FactStore) -> None:
    assert uncovered_facts(store, [entry(scope_paths=["services/api/app/a.py"], facts=[])]) == [
        "decision:ADR-0007",
        "module:services/api/app/b.py",
    ]


def test_stored_paths_must_live_under_paths() -> None:
    from codetrail.generate.outline import outline_paths

    stored = {"paths": [{"id": "areas/api", "title": "x", "goal": "", "steps": ["areas/api"]},
                        {"id": "paths/ok", "title": "y", "goal": "", "steps": ["areas/api"]}]}  # fmt: skip
    assert [path.id for path in outline_paths(stored, {"areas/api"})] == ["paths/ok"]


def test_a_title_a_terminal_would_act_on_is_dropped(
    store: FactStore, manifest: SourceManifest, scanner: SecretScanner
) -> None:
    raw = [{"id": "areas/api", "kind": "area", "title": "The API\x1b[2A$0.01", "scope_paths": ["services/api"]}]
    entries, problems = validate_outline(raw, store, manifest, scanner)
    assert entries == [] and "control character" in problems[0] and "\x1b" not in problems[0]


def test_an_entry_holding_a_secret_is_dropped_naming_only_the_rule(
    store: FactStore, manifest: SourceManifest, scanner: SecretScanner
) -> None:
    token = fake_github_token(41)
    raw = [
        {"id": "areas/api", "kind": "area", "title": f"The API ({token})", "scope_paths": ["services/api"]},
        {"id": "concepts/rest", "kind": "concept", "title": "REST", "scope_paths": ["docs"], "facts": [token]},
        {"id": "concepts/kept", "kind": "concept", "title": "Kept", "scope_paths": ["docs"]},
    ]
    entries, problems = validate_outline(raw, store, manifest, scanner)
    assert [entry.id for entry in entries] == ["concepts/kept"]
    assert len(problems) == 2 and all("looks like a secret (github-pat)" in problem for problem in problems)
    assert token not in " ".join(problems)


def test_a_path_holding_a_secret_or_a_control_character_is_dropped(scanner: SecretScanner) -> None:
    token = fake_github_token(42)
    raw = [
        {"id": "paths/start", "title": "Start", "goal": f"Learn to use {token}.", "steps": ["areas/api"]},
        {"id": "paths/escape", "title": "Start\x1b[2A", "goal": "Learn.", "steps": ["areas/api"]},
        {"id": "paths/kept", "title": "Kept", "goal": "Learn.", "steps": ["areas/api"]},
    ]
    paths, problems = validate_paths(raw, {"areas/api"}, scanner)
    assert [path.id for path in paths] == ["paths/kept"]
    assert "looks like a secret (github-pat)" in problems[0] and token not in problems[0]
    assert "control character" in problems[1] and "\x1b" not in problems[1]
