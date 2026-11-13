"""Codetrail's mirror of a target: a bare clone, fetched over the file:// transport (design section 3.2).

The target is only ever read, by git's upload-pack: no command runs inside the target, and `--no-local` stops a clone
from hardlinking the target's object files.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from codetrail.errors import CodetrailError
from codetrail.repo.git import run_git

# git checks every object a clone or fetch receives and refuses a malformed one, rather than storing it in the mirror.
CHECK_OBJECTS = ("-c", "transfer.fsckObjects=true")


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    blob: str
    path: str
    size: int | None = None  # a blob's, from the tree listing; None for a submodule


def repository_url(path: Path) -> str:
    return path.expanduser().resolve().as_uri()


def check_branch(repository: Path, branch: str) -> None:
    """Fails unless the repository has the branch; reads it over file:// only."""
    found = run_git(
        ["ls-remote", "--heads", "--", repository_url(repository), f"refs/heads/{branch}"],
        allowed_exit_codes=(0, 2),
    )
    if not found.strip():
        raise CodetrailError(f"{repository} has no branch {branch!r}.")


def refresh_mirror(mirror: Path, repository: Path, branch: str) -> str:
    """Clones or fetches a local target's branch into the mirror over file://; see `refresh_mirror_from`."""
    check_branch(repository, branch)
    return refresh_mirror_from(mirror, repository_url(repository), branch)


def refresh_mirror_from(
    mirror: Path,
    url: str,
    branch: str,
    *,
    git_options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
) -> str:
    """Clones or fetches the branch into the mirror, with the tags that point into it, and returns its head commit.

    The branch is fetched without tags; then the mirror's tags that the target no longer has, or that point elsewhere
    now, are deleted, and a second fetch of the branch brings the rest by git's tag auto-follow, which takes only tags
    on objects the mirror holds, so no other branch's commits come in (design section 19.2). Its errors are replaced,
    since they name tags.
    """
    git_options = [*CHECK_OBJECTS, *git_options]
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        run_git(
            [
                *git_options,
                "clone",
                "--quiet",
                "--bare",
                "--no-local",
                "--no-tags",
                "--single-branch",
                "--branch",
                branch,
                "--",
                url,
                str(mirror),
            ],
            environment=environment,
        )
    refspec = f"+refs/heads/{branch}:refs/heads/{branch}"
    run_git([*git_options, "fetch", "--quiet", "--no-tags", "--prune", "--", url, refspec], git_dir=mirror,
            environment=environment)  # fmt: skip
    _drop_stale_tags(mirror, url, git_options, environment)
    try:
        # Fetching the branch again, now up to date, brings only the tags git's auto-follow takes.
        run_git([*git_options, "fetch", "--quiet", "--", url, refspec], git_dir=mirror, environment=environment)
    except CodetrailError:
        # git's message names the tag it couldn't write, which no scan has passed yet; the update's error shows it.
        raise CodetrailError("git couldn't fetch the branch's tags into the mirror.") from None
    return head_commit(mirror, branch)


def _drop_stale_tags(mirror: Path, url: str, git_options: Sequence[str], environment: Mapping[str, str] | None) -> None:
    """Deletes the mirror's tags that the target no longer has or that point elsewhere now; touches only refs/tags/."""
    listed = run_git([*git_options, "ls-remote", "--tags", "--", url], environment=environment).decode(
        "utf-8", "surrogateescape"
    )
    remote = {}
    for line in listed.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            remote[ref] = sha
    local = run_git(["for-each-ref", "--format=%(refname)%00%(objectname)", "refs/tags"], git_dir=mirror)
    stale = []
    for line in local.decode("utf-8", "surrogateescape").splitlines():
        ref, _, sha = line.partition("\0")
        if ref.startswith("refs/tags/") and remote.get(ref) != sha:
            stale.append(f"delete {ref}\0{sha}\0")
    if stale:
        try:
            run_git(["update-ref", "-z", "--stdin"], git_dir=mirror,
                    input="".join(stale).encode("utf-8", "surrogateescape"))  # fmt: skip
        except CodetrailError:
            # git's message names the tag, which no scan has passed yet; the update's error would show it.
            raise CodetrailError(f"git couldn't remove {len(stale)} stale tag(s) from the mirror.") from None


def head_commit(mirror: Path, branch: str) -> str:
    return run_git(["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"], git_dir=mirror).decode().strip()


def list_tree(mirror: Path, commit: str) -> list[TreeEntry]:
    """Every entry of the commit's tree, with each blob's size, read from the objects' headers without their
    content."""
    output = run_git(["ls-tree", "-r", "-z", "--long", "--full-tree", commit], git_dir=mirror)
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, _kind, blob, size = header.decode().split()
        entries.append(TreeEntry(mode=mode, blob=blob, path=path.decode("utf-8", "surrogateescape"),
                                 size=None if size == "-" else int(size)))  # fmt: skip
    return entries


def read_file_at(mirror: Path, commit: str, path: str, *, max_bytes: int) -> bytes | None:
    """The file's content at the commit, or None when it doesn't exist there; a file over `max_bytes` raises, unread."""
    try:
        size = int(run_git(["cat-file", "-s", f"{commit}:{path}"], git_dir=mirror))
    except CodetrailError:
        return None
    if size > max_bytes:
        raise CodetrailError(
            f"The target's {path} is {size} bytes at commit {commit[:12]}; Codetrail reads at most {max_bytes} "
            "([source] max_file_bytes)."
        )
    try:
        return run_git(["cat-file", "blob", f"{commit}:{path}"], git_dir=mirror)
    except CodetrailError:
        return None


def read_blobs(mirror: Path, blobs: list[str]) -> Iterator[tuple[str, bytes]]:
    """Each blob's content, in order, read with one `git cat-file --batch`."""
    if not blobs:
        return
    output = run_git(["cat-file", "--batch"], git_dir=mirror, input="".join(f"{blob}\n" for blob in blobs).encode())
    position = 0
    for blob in blobs:
        header_end = output.index(b"\n", position)
        name, kind, size = output[position:header_end].decode().split(" ")
        if name != blob or kind != "blob":
            raise CodetrailError(f"git cat-file returned {kind} {name} for blob {blob}")
        start = header_end + 1
        yield blob, output[start : start + int(size)]
        position = start + int(size) + 1
