"""The "you're behind" signal: what landed since the last update, from git alone (design section 8.5).

It fetches into the mirror without rebuilding `source/`, under the target's lock taken without waiting: while an
update runs, the signal says so instead. Areas are the top-level folders of visible changed paths; excluded paths
are never counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codetrail.config import Paths, check_containment, load_global, load_target
from codetrail.database import connect
from codetrail.facts.store import FactStore
from codetrail.lock import TargetBusy, target_lock
from codetrail.repo.history import changed_paths, commit_count, is_ancestor, merges_between
from codetrail.repo.refresh import target_rules
from codetrail.repo.remote import fetch_target
from codetrail.repo.source import SourceManifest


@dataclass(frozen=True)
class Signal:
    updated_commit: str | None = None
    head: str | None = None
    merges: int = 0
    commits: int = 0
    areas: list[str] = field(default_factory=list)
    updating: bool = False


def behind(paths: Paths, name: str, since: str | None = None) -> Signal:
    """What landed since the last update. `since`, the commit the guide last covered, measures from there instead,
    when it is in the branch's history: a catch-up that missed the target moved its snapshot, not its guide."""
    target = load_target(paths, name)
    if target.repository is not None:
        check_containment(paths, target.repository)
    data = paths.target_data(name)
    try:
        with target_lock(paths, name):
            mirror = data / "mirror.git"
            settings = load_global(paths)
            head = fetch_target(mirror, target, stall_seconds=settings.hosting.timeout_seconds)
            snapshot = _latest_snapshot_commit(paths, name)
            if snapshot is None:
                return Signal(head=head)
            if since is not None and is_ancestor(mirror, since, head):
                snapshot = since
            rules = target_rules(paths, name, mirror, head, settings.source)
            manifest = SourceManifest.load(data / "source.json")
            flagged = manifest.excluded_paths() if manifest else set()
            visible = [path for path in changed_paths(mirror, snapshot, head)
                       if rules.reason(path) is None and path not in flagged]  # fmt: skip
            areas = sorted({path.split("/", 1)[0] for path in visible if "/" in path})
            return Signal(snapshot, head, merges_between(mirror, snapshot, head), commit_count(mirror, snapshot, head),
                          areas)  # fmt: skip
    except TargetBusy:
        return Signal(updating=True)


def _latest_snapshot_commit(paths: Paths, name: str) -> str | None:
    database = paths.target_data(name) / "codetrail.db"
    if not database.exists():
        return None
    connection = connect(database)
    try:
        snapshot = FactStore(connection).latest_snapshot()
    finally:
        connection.close()
    return snapshot.commit if snapshot else None
