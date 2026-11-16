"""One update at a time per target (design section 10), and no removal while a command uses the target.

The OS releases the locks if the process dies."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from codetrail.config import Paths, validate_target_name, validate_team_name
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
            raise TargetBusy(_already_updating(name)) from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def existing_target_lock(paths: Paths, name: str) -> Iterator[None]:
    """Holds the update lock if the data folder has one, creating nothing and following no symlink.

    For removal: an update holds it even when an editor has replaced the settings file its in-use lock is on."""
    with _existing_lock(
        paths.target_data(name), "update.lock", f"target {name!r}", fcntl.LOCK_EX, _already_updating(name)
    ):
        yield


@contextmanager
def _existing_lock(folder: Path, file: str, what: str, operation: int, busy: str) -> Iterator[None]:
    descriptor = None
    if folder.is_dir() and not folder.is_symlink():
        try:
            descriptor = os.open(folder / file, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise CodetrailError(f"Can't lock {what} ({folder / file}): {error.strerror}.") from error
    if descriptor is None:
        yield
        return
    try:
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TargetBusy(busy) from error
        yield
    finally:
        os.close(descriptor)  # closing releases the lock


def _already_updating(name: str) -> str:
    return f"Target {name!r} is already updating; try again when that finishes."


def _target_lock(paths: Paths, name: str, operation: int, busy: str) -> AbstractContextManager[None]:
    settings = paths.target_file(validate_target_name(name))
    missing = f"No target named {name!r}. Add it with: codetrail target add {name} <path>"
    return _settings_lock(settings, f"target {name!r}", missing, operation, busy)


@contextmanager
def target_in_use(paths: Paths, name: str) -> Iterator[None]:
    """Held, shared, by `serve`, `update` and `files` while they use the target, so it isn't removed under them."""
    with _target_lock(paths, name, fcntl.LOCK_SH, f"Target {name!r} is being removed."):
        yield


@contextmanager
def target_removal(paths: Paths, name: str) -> Iterator[None]:
    """Held alone by `codetrail target remove` until the target's settings file is gone."""
    busy = (
        f"Target {name!r} is in use: stop the Codetrail server serving it (`codetrail serve` or "
        "`codetrail team serve`), or wait for its update to finish."
    )
    with _target_lock(paths, name, fcntl.LOCK_EX, busy):
        yield


def _team_lock(paths: Paths, name: str, operation: int, busy: str) -> AbstractContextManager[None]:
    settings = paths.team_file(validate_team_name(name))
    return _settings_lock(settings, f"team {name!r}", f"No team named {name!r}.", operation, busy)


@contextmanager
def team_in_use(paths: Paths, name: str) -> Iterator[None]:
    """Held, shared, by `team serve`, every team sync and every save of the team's settings.

    Two locks: the settings file's, which a save replaces, and `in-use.lock` in the team's data folder, which stays,
    so a removal is refused after a save too."""
    with _team_lock(paths, name, fcntl.LOCK_SH, f"Team {name!r} is being removed."):
        folder = paths.team_data(name)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "in-use.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise TargetBusy(f"Team {name!r} is being removed.") from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def team_removal(paths: Paths, name: str) -> Iterator[None]:
    """Held alone by `codetrail team remove` until the team's settings file is gone."""
    busy = f"Team {name!r} is in use: stop `codetrail team serve` for it, or wait for its sync to finish."
    with (
        _team_lock(paths, name, fcntl.LOCK_EX, busy),
        _existing_lock(paths.team_data(name), "in-use.lock", f"team {name!r}", fcntl.LOCK_EX, busy),
    ):
        yield


@contextmanager
def _settings_lock(settings: Path, what: str, missing: str, operation: int, busy: str) -> Iterator[None]:
    """Locks the settings file, which removal deletes last; opening it creates no folder."""
    try:
        handle = settings.open("rb")
    except FileNotFoundError as error:
        raise CodetrailError(missing) from error
    except OSError as error:
        raise CodetrailError(f"Can't lock {what} ({settings}): {error.strerror}.") from error
    with handle:
        try:
            fcntl.flock(handle, operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TargetBusy(busy) from error
        except OSError as error:  # a filesystem without locks, for one
            raise CodetrailError(f"Can't lock {what} ({settings}): {error.strerror}.") from error
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


class TeamBusy(CodetrailError):
    """Another catch-up holds the team's lock."""


@contextmanager
def team_catch_up_lock(paths: Paths, name: str) -> Iterator[None]:
    """One catch-up at a time per team, whichever command or page started it."""
    folder = paths.team_data(validate_team_name(name))
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "catch-up.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TeamBusy(f"Team {name!r} is already catching up.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
