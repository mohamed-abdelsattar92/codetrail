"""The extractor interface and the runner that merges and resolves their facts (design section 5.1)."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from codetrail.extract import Extractor, FileFacts, Reference, Resolution, run_extractors
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
    extraction = run_extractors(tmp_path, paths, [Long()], max_attribute_chars=10)
    assert dict(extraction.entities[0].attributes) == {"text": "x" * 9 + "…", "list": ["y" * 9 + "…"]}
