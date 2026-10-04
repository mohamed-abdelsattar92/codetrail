"""One update at a time per target (design section 10). The OS releases the lock if the process dies."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager

from codetrail.config import Paths
from codetrail.errors import CodetrailError


class TargetBusy(CodetrailError):
    """Another update holds the target's lock."""


@contextmanager
def target_lock(paths: Paths, name: str) -> Iterator[None]:
    folder = paths.target_data(name)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "update.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TargetBusy(f"Target {name!r} is already updating; try again when that finishes.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
