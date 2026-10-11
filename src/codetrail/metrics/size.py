"""Code size: files and lines by language, documents and tests (design section 19.1).

A file's language is looked up by its name, then its suffix, in the configured map: a dictionary lookup, never a
pattern a repository could control.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from pathspec import GitIgnoreSpec


@dataclass(frozen=True)
class LanguageSize:
    language: str
    files: int
    lines: int


@dataclass(frozen=True)
class FileSize:
    path: str
    language: str
    lines: int


@dataclass(frozen=True)
class CodeSize:
    files: int  # every allowed file
    languages: list[LanguageSize]  # the most lines first
    documents: LanguageSize
    other_files: int  # in no language
    binary_files: int
    too_large_files: int  # over extract.max_file_bytes, or unreadable
    code_lines: int  # lines in files with a language, tests included
    test_lines: int
    largest: list[FileSize]  # files with a language, the most lines first


def count_lines(content: bytes) -> int:
    """The newline bytes, plus one for a last line without one."""
    return content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)


def language_of(path: str, languages: Mapping[str, str]) -> str | None:
    name = PurePosixPath(path).name.lower()
    if not name.startswith(".") and name in languages:
        return languages[name]
    suffix = PurePosixPath(name).suffix
    return languages.get(suffix) if suffix else None


def measure_size(
    paths: Iterable[str],
    read: Callable[[str], bytes | None],
    languages: Mapping[str, str],
    documents: GitIgnoreSpec,
    tests: GitIgnoreSpec,
) -> CodeSize:
    """Reads each allowed file once and sorts it: too large, binary, a document, a language or other."""
    files = other = binary = too_large = document_files = document_lines = test_lines = 0
    by_language: dict[str, list[int]] = {}
    largest = []
    for path in paths:
        files += 1
        content = read(path)
        if content is None:
            too_large += 1
            continue
        if b"\0" in content:
            binary += 1
            continue
        lines = count_lines(content)
        if documents.match_file(path):
            document_files += 1
            document_lines += lines
            continue
        language = language_of(path, languages)
        if language is None:
            other += 1
            continue
        counts = by_language.setdefault(language, [0, 0])
        counts[0] += 1
        counts[1] += lines
        largest.append(FileSize(path, language, lines))
        if tests.match_file(path):
            test_lines += lines
    sizes = [LanguageSize(language, counts[0], counts[1]) for language, counts in by_language.items()]
    sizes.sort(key=lambda size: (-size.lines, -size.files, size.language))
    largest.sort(key=lambda file: (-file.lines, file.path))
    return CodeSize(files, sizes, LanguageSize("Documents", document_files, document_lines), other, binary,
                    too_large, sum(size.lines for size in sizes), test_lines, largest)  # fmt: skip
