"""The extractor interface and the runner that merges and resolves their facts (design section 5.1)."""

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
import yaml

from codetrail.extract import Extractor, FileFacts, Reference, Resolution, load_yaml_without_aliases, run_extractors
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source


class Words:
    """A fake extractor: each .txt file is a module; each word 'uses:<path>' references another file."""

    name = "words"
    version = 1

    def __init__(self) -> None:
        self.prepared: list[str] = []

    def handles(self, path: str) -> bool:
        return path.endswith(".txt")

    def prepare(self, paths: Sequence[str]) -> None:
        self.prepared = list(paths)

    def extract(self, path: str, content: bytes) -> FileFacts:
        text = content.decode()
        if "explode" in text:
            raise ValueError("cannot read " + text)
        module = Entity(f"module:{path}", EntityKind.MODULE, {"name": path}, (Source(path),))
        shared = Entity("package:pypi/shared", EntityKind.PACKAGE, {"colour": text.split()[0]}, (Source(path),))
        references = tuple(
            Reference(module.id, RelationKind.IMPORTS, word.removeprefix("uses:"), (Source(path, 1, 1),))
            for word in text.split()
            if word.startswith("uses:")
        )
        return FileFacts(path, (module, shared), references)

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        relations, unresolved = [], 0
        for file in files:
            for reference in file.references:
                target = f"module:{reference.target}"
                if target in known:
                    relations.append(Relation(reference.source_id, reference.kind, target, {}, reference.sources))
                else:
                    unresolved += 1
        relations.append(Relation("module:a.txt", RelationKind.IMPORTS, "module:ghost.txt"))
        return Resolution(relations, unresolved)


def write(source: Path, files: dict[str, str]) -> list[str]:
    for path, text in files.items():
        (source / path).parent.mkdir(parents=True, exist_ok=True)
        (source / path).write_text(text)
    return sorted(files)


def test_runs_merges_and_resolves(tmp_path: Path) -> None:
    paths = write(tmp_path, {"a.txt": "red uses:b.txt uses:missing.txt", "b.txt": "red", "c.md": "ignored"})
    extractor = Words()
    extraction = run_extractors(tmp_path, paths, [extractor])
    assert extractor.prepared == ["a.txt", "b.txt"]
    assert sorted(entity.id for entity in extraction.entities) == [
        "module:a.txt",
        "module:b.txt",
        "package:pypi/shared",
    ]
    shared = next(entity for entity in extraction.entities if entity.id == "package:pypi/shared")
    assert shared.sources == (Source("a.txt"), Source("b.txt"))
    assert [relation.key for relation in extraction.relations] == [("module:a.txt", "imports", "module:b.txt")]
    assert extraction.unresolved == {"words": 2}  # missing.txt, and the dangling ghost relation
    assert extraction.warnings == []


def test_conflicting_attributes_keep_the_first_and_warn(tmp_path: Path) -> None:
    paths = write(tmp_path, {"a.txt": "red", "b.txt": "blue"})
    extraction = run_extractors(tmp_path, paths, [Words()])
    shared = next(entity for entity in extraction.entities if entity.id == "package:pypi/shared")
    assert shared.attributes == {"colour": "red"}
    assert extraction.warnings == ["words: package:pypi/shared has different attributes in b.txt; kept a.txt's"]


def test_an_unreadable_file_is_a_warning_without_its_content(tmp_path: Path) -> None:
    paths = write(tmp_path, {"a.txt": "red", "bad.txt": "explode secret-ish"})
    extraction = run_extractors(tmp_path, paths, [Words()])
    assert extraction.warnings == ["words: bad.txt: could not be read (ValueError)"]
    assert "secret-ish" not in " ".join(extraction.warnings)
    assert "module:a.txt" in {entity.id for entity in extraction.entities}


def test_only_listed_paths_are_read(tmp_path: Path) -> None:
    write(tmp_path, {"a.txt": "red", "stray.txt": "red"})
    extraction = run_extractors(tmp_path, ["a.txt"], [Words()])
    assert "module:stray.txt" not in {entity.id for entity in extraction.entities}


def test_the_fake_satisfies_the_protocol() -> None:
    extractor: Extractor = Words()
    assert extractor.name == "words"


def test_files_over_the_size_limit_are_skipped_with_a_warning(tmp_path: Path) -> None:
    paths = write(tmp_path, {"a.txt": "red", "big.txt": "red " * 100})
    extraction = run_extractors(tmp_path, paths, [Words()], max_file_bytes=50)
    assert extraction.warnings == ["words: big.txt: skipped, larger than 50 bytes"]
    assert "module:big.txt" not in {entity.id for entity in extraction.entities}


class Dates(Words):
    def extract(self, path: str, content: bytes) -> FileFacts:
        import datetime

        return FileFacts(path, (Entity(f"module:{path}", EntityKind.MODULE, {"when": datetime.date(2026, 1, 1)}),))


def test_attributes_that_are_not_json_become_a_warning(tmp_path: Path) -> None:
    paths = write(tmp_path, {"a.txt": "red"})
    extraction = run_extractors(tmp_path, paths, [Dates()])
    assert extraction.entities == []
    assert extraction.warnings == ["words: a.txt: could not be read (TypeError)"]


class Long(Words):
    def extract(self, path: str, content: bytes) -> FileFacts:
        return FileFacts(path, (Entity(f"module:{path}", EntityKind.MODULE, {"text": "x" * 50, "list": ["y" * 50]}),))


def test_long_text_attributes_are_cut(tmp_path: Path) -> None:
    """Text from a target file reaches prompts and pages, so it's bounded (Phase 7 review, finding 3)."""
    paths = write(tmp_path, {"a.txt": "red"})
    extraction = run_extractors(tmp_path, paths, [Long()], max_attribute_chars=20)
    assert dict(extraction.entities[0].attributes) == {"text": "x" * 19 + "…", "list": ["y" * 19 + "…"]}


def test_facts_with_overlong_ids_are_skipped_with_a_warning(tmp_path: Path) -> None:
    """Ids carry target text too, so they share the attribute limit (Phase 7 review, notes)."""
    paths = write(tmp_path, {"a.txt": "red", ("b" * 40) + ".txt": "red"})
    extraction = run_extractors(tmp_path, paths, [Words()], max_attribute_chars=30)
    assert "module:a.txt" in {entity.id for entity in extraction.entities}
    assert all(len(entity.id) <= 30 for entity in extraction.entities)
    assert f"words: {'b' * 40}.txt: skipped a fact whose id is longer than 30 characters" in extraction.warnings


class BreaksInPrepare(Words):
    name = "prepare-breaks"

    def prepare(self, paths: Sequence[str]) -> None:
        raise ValueError("a hostile config")


class BreaksInResolve(Words):
    name = "resolve-breaks"

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        raise TypeError("paths was a number")


def test_an_extractor_failing_to_prepare_or_resolve_is_a_warning(tmp_path: Path) -> None:
    paths = write(tmp_path, {"a.txt": "red uses:b.txt", "b.txt": "blue"})
    extraction = run_extractors(tmp_path, paths, [BreaksInPrepare(), BreaksInResolve(), Words()])
    assert any("prepare-breaks" in warning and "ValueError" in warning for warning in extraction.warnings)
    assert any("resolve-breaks" in warning and "TypeError" in warning for warning in extraction.warnings)
    assert ("module:a.txt", "module:b.txt") in {(r.source_id, r.target_id) for r in extraction.relations}
    assert not any("hostile" in warning or "number" in warning for warning in extraction.warnings)  # no content


def test_derived_facts_get_the_same_checks() -> None:
    from codetrail.extract import check_facts

    entities = [
        Entity("part:ok", EntityKind.PART, {"name": "x" * 500, "list": ["y" * 500]}),
        Entity("part:" + "z" * 400, EntityKind.PART, {}),
        Entity("part:odd", EntityKind.PART, {"bad": object()}),
    ]
    relations = [Relation("part:ok", RelationKind.DEPENDS_ON, "part:odd", {"source": "s" * 500})]
    kept, related, warnings = check_facts(entities, relations, 300)
    assert [entity.id for entity in kept] == ["part:ok"]
    assert len(kept[0].attributes["name"]) == 300 and len(kept[0].attributes["list"][0]) == 300
    assert related == []  # its target was dropped, so the relation goes too
    assert len(warnings) == 2 and not any("zzz" in warning for warning in warnings)


def test_yaml_is_read_in_one_pass_without_aliases() -> None:
    root, data = load_yaml_without_aliases("a: [1, 2]\nb: text\n")
    assert data == {"a": [1, 2], "b": "text"}
    assert isinstance(root, yaml.MappingNode) and root.value[1][0].start_mark.line == 1
    assert load_yaml_without_aliases("") == (None, None)
    with pytest.raises(yaml.YAMLError):
        load_yaml_without_aliases("a: &shared [1]\nb: *shared\n")
    with pytest.raises(yaml.YAMLError):
        load_yaml_without_aliases("!!python/object:os.system x\n")


def test_base_60_integers_stay_text_so_none_costs_quadratic_time() -> None:
    import time

    started = time.monotonic()
    _, data = load_yaml_without_aliases("long: " + "1:" * 200_000 + "1\nshort: 1:30\ntagged: !!int 2:00\nplain: 12\n")
    assert time.monotonic() - started < 2
    assert (data["short"], data["tagged"], data["plain"]) == ("1:30", "2:00", 12)
