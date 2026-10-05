"""The guide: the knowledge base Claude writes, as Markdown with YAML front matter in its own git repository.

It lives in Codetrail's data folder, never in a target. An update writes pages into the working tree and commits once;
if anything fails, the uncommitted changes are discarded (design section 6.8). Front matter is read with
`yaml.safe_load`, so no tag in it can build an object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from codetrail.errors import CodetrailError
from codetrail.repo.git import run_git

PAGE_ID = re.compile(r"(areas|concepts|paths|digests|answers)/[a-z0-9][a-z0-9-]{0,80}")
OUTLINE_FILE = "outline.yaml"
COMMIT_ID = re.compile(r"[0-9a-f]{7,40}")
# The guide's own commits run no hooks and need no signing key, whatever the user's global git settings say.
COMMIT_SETTINGS = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                   "-c", "user.name=Codetrail", "-c", "user.email=codetrail@localhost"]  # fmt: skip


@dataclass(frozen=True)
class Page:
    id: str
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def kind(self) -> str:
        return str(self.meta.get("kind", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title", self.id))


def validate_page_id(page_id: str) -> str:
    if not PAGE_ID.fullmatch(page_id):
        raise CodetrailError(f"{page_id!r} isn't a valid page id.")
    return page_id


class GuideRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def git_dir(self) -> Path:
        return self.root / ".git"

    def ensure(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        run_git(["init", "-q", "-b", "main", str(self.root)])

    def _git(self, *arguments: str) -> bytes:
        return run_git([*COMMIT_SETTINGS, *arguments], git_dir=self.git_dir, work_tree=self.root)

    def path_of(self, page_id: str) -> Path:
        return self.root / f"{validate_page_id(page_id)}.md"

    def read_page(self, page_id: str) -> Page | None:
        file = self.path_of(page_id)
        if not file.exists():
            return None
        return parse_page(page_id, file.read_text(encoding="utf-8"))

    def write_page(self, page: Page) -> None:
        file = self.path_of(page.id)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(render_page(page), encoding="utf-8")

    def pages(self, kind: str | None = None) -> list[Page]:
        found = []
        for file in sorted(self.root.glob("*/*.md")):
            page_id = f"{file.parent.name}/{file.stem}"
            if PAGE_ID.fullmatch(page_id):
                page = parse_page(page_id, file.read_text(encoding="utf-8"))
                if kind is None or page.kind == kind:
                    found.append(page)
        return found

    def read_outline(self) -> dict[str, Any] | None:
        file = self.root / OUTLINE_FILE
        if not file.exists():
            return None
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def write_outline(self, outline: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(outline, sort_keys=False, allow_unicode=True)
        (self.root / OUTLINE_FILE).write_text(text, encoding="utf-8")

    def has_uncommitted_changes(self) -> bool:
        return bool(self._git("status", "--porcelain", "--untracked-files=all").strip())

    def head(self) -> str | None:
        try:
            return self._git("rev-parse", "--verify", "-q", "HEAD").decode().strip() or None
        except CodetrailError:
            return None

    def commit(self, message: str) -> str | None:
        """Commits every change and returns the commit, or None when nothing changed."""
        self._git("add", "-A")
        if not self._git("status", "--porcelain").strip():
            return None
        self._git("commit", "-q", "-m", message)
        return self.head()

    def discard(self) -> None:
        """Throws away every uncommitted change, back to the last commit."""
        if self.head() is not None:
            self._git("reset", "-q", "--hard", "HEAD")
        self._git("clean", "-q", "-f", "-d")

    def file_at(self, commit: str, page_id: str) -> str | None:
        if not COMMIT_ID.fullmatch(commit):  # only a commit id reaches git, never an option
            return None
        try:
            return self._git("show", f"{commit}:{validate_page_id(page_id)}.md").decode("utf-8", "replace")
        except CodetrailError:
            return None


def render_page(page: Page) -> str:
    front = yaml.safe_dump(page.meta, sort_keys=False, allow_unicode=True)
    return f"---\n{front}---\n\n{page.body.rstrip()}\n"


def parse_page(page_id: str, text: str) -> Page:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta = yaml.safe_load(text[4 : end + 1]) or {}
            body = text[end + 5 :].lstrip("\n").rstrip()
            return Page(page_id, meta if isinstance(meta, dict) else {}, body)
    return Page(page_id, {}, text.rstrip())
