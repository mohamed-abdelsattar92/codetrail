"""Facts by kind, and the dependencies that arrived and left, from the validity ranges (design section 19.1).

Codetrail's history of a repository starts with its first snapshot: packages present then were there from the start,
not arrivals.
"""

from __future__ import annotations

from dataclasses import dataclass

from codetrail.facts import EntityKind, Snapshot
from codetrail.facts.store import FactStore


@dataclass(frozen=True)
class KindCount:
    kind: str
    count: int
    change: int | None  # since the previous snapshot
    trend: list[int]  # over the last snapshots, oldest first


@dataclass(frozen=True)
class Change:
    package: str
    snapshot: Snapshot  # the snapshot that first saw it, or first no longer saw it


@dataclass(frozen=True)
class Inventory:
    since: Snapshot | None  # the first snapshot: where Codetrail's history of the repository starts
    kinds: list[KindCount]  # the most facts first
    arrived: list[Change]  # newest first
    left: list[Change]  # newest first


def measure_inventory(store: FactStore, trend_updates: int) -> Inventory:
    snapshots = store.snapshots()
    if not snapshots:
        return Inventory(None, [], [], [])
    recent = snapshots[-trend_updates:]
    counts = store.entity_counts([snapshot.id for snapshot in recent])
    latest = counts[recent[-1].id]
    previous = counts[recent[-2].id] if len(recent) > 1 else None
    kinds = [
        KindCount(kind, count, None if previous is None else count - previous.get(kind, 0),
                  [counts[snapshot.id].get(kind, 0) for snapshot in recent])
        for kind, count in sorted(latest.items(), key=lambda item: (-item[1], item[0]))
    ]  # fmt: skip
    by_id = {snapshot.id: snapshot for snapshot in snapshots}
    arrived, left = [], []
    for package, first, last in store.entity_spans(EntityKind.PACKAGE):
        if first > snapshots[0].id:
            arrived.append(Change(package, by_id[first]))
        if last is not None:
            after = next((snapshot for snapshot in snapshots if snapshot.id > last), None)
            if after is not None:
                left.append(Change(package, after))
    return Inventory(snapshots[0], kinds, sorted(arrived, key=_newest_first), sorted(left, key=_newest_first))


def _newest_first(change: Change) -> tuple[int, str]:
    return -change.snapshot.id, change.package
