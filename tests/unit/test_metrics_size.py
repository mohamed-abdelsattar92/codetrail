"""Code size by language, documents and tests (design section 19.1)."""

from pathspec import GitIgnoreSpec

from codetrail.metrics.size import count_lines, language_of, measure_size

LANGUAGES = {".py": "Python", ".ts": "TypeScript", "dockerfile": "Dockerfile"}


def spec(lines: list[str]) -> GitIgnoreSpec:
    return GitIgnoreSpec.from_lines(lines)


def test_lines_count_a_last_line_without_a_newline() -> None:
    assert [count_lines(content) for content in (b"", b"a", b"a\n", b"a\n\n", b"a\nb")] == [0, 1, 1, 2, 2]


def test_a_language_comes_from_the_file_name_then_its_suffix() -> None:
    assert language_of("ops/Dockerfile", LANGUAGES) == "Dockerfile"
    assert language_of("app/Main.PY", LANGUAGES) == "Python"
    assert language_of("data.csv", LANGUAGES) is None
    assert language_of(".py", LANGUAGES) is None  # a dot file has no suffix


def test_files_are_sorted_into_languages_documents_and_the_rest() -> None:
    files: dict[str, bytes | None] = {
        "app/main.py": b"a\nb\nc",
        "tests/test_main.py": b"a\n",
        "web/x.ts": b"",
        "Dockerfile": b"FROM x\n",
        "README.md": b"# Hi\n\nText\n",
        "docs/notes.py": b"x\n",
        "logo.png": b"\x89PNG\0\0",
        "data.csv": b"a,b\n",
        "big.py": None,
    }
    size = measure_size(files, files.get, LANGUAGES, spec(["README*", "docs/**"]), spec(["tests/**"]))
    assert size.files == 9
    assert [(item.language, item.files, item.lines) for item in size.languages] == [
        ("Python", 2, 4),
        ("Dockerfile", 1, 1),
        ("TypeScript", 1, 0),
    ]
    assert (size.documents.files, size.documents.lines) == (2, 4)
    assert (size.other_files, size.binary_files, size.too_large_files) == (1, 1, 1)
    assert (size.code_lines, size.test_lines) == (5, 1)
    assert [item.path for item in size.largest] == ["app/main.py", "Dockerfile", "tests/test_main.py", "web/x.ts"]


def test_an_empty_source_measures_nothing() -> None:
    size = measure_size({}, lambda path: None, LANGUAGES, spec([]), spec([]))
    assert (size.files, size.code_lines, size.languages, size.largest) == (0, 0, [], [])
