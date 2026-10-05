"""One update at a time per target (design section 10), and no removal while a command uses the target.

The OS releases the locks if the process dies."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from codetrail.config import Paths, validate_target_name
from codetrail.errors import CodetrailError


class TargetBusy(CodetrailError):
    """Another command holds the target's lock."""


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


@contextmanager
def target_in_use(paths: Paths, name: str) -> Iterator[None]:
    """Held, shared, by `serve`, `update` and `files` while they use the target, so it isn't removed under them."""
    with _settings_lock(paths, name, fcntl.LOCK_SH, f"Target {name!r} is being removed."):
        yield


@contextmanager
def target_removal(paths: Paths, name: str) -> Iterator[None]:
    """Held alone by `codetrail target remove` until the target's settings file is gone."""
    busy = f"Target {name!r} is in use: stop `codetrail serve` for it, or wait for its update to finish."
    with _settings_lock(paths, name, fcntl.LOCK_EX, busy):
        yield


@contextmanager
def _settings_lock(paths: Paths, name: str, operation: int, busy: str) -> Iterator[None]:
    """Locks the target's settings file, which removal deletes last; opening it creates no folder."""
    settings = paths.target_file(validate_target_name(name))
    missing = f"No target named {name!r}. Add it with: codetrail target add {name} <path>"
    try:
        handle = settings.open("rb")
    except FileNotFoundError as error:
        raise CodetrailError(missing) from error
    except OSError as error:
        raise CodetrailError(f"Can't lock target {name!r} ({settings}): {error.strerror}.") from error
    with handle:
        try:
            fcntl.flock(handle, operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TargetBusy(busy) from error
        except OSError as error:  # a filesystem without locks, for one
            raise CodetrailError(f"Can't lock target {name!r} ({settings}): {error.strerror}.") from error
        try:
            if not _is_current(handle.fileno(), settings):  # a removal finished between opening and locking
                raise CodetrailError(missing)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _is_current(descriptor: int, settings: Path) -> bool:
    try:
        current = settings.stat()
    except FileNotFoundError:
        return False
    opened = os.fstat(descriptor)
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
