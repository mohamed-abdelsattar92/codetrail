"""Facts by kind and the dependencies that arrived and left (design section 19.1)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind
from codetrail.facts.store import FactStore
from codetrail.metrics.inventory import Change, KindCount, measure_inventory


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    return FactStore(connect(tmp_path / "codetrail.db"))


def package(name: str) -> Entity:
    return Entity(f"package:{name}", EntityKind.PACKAGE)


def module(name: str) -> Entity:
    return Entity(f"module:{name}", EntityKind.MODULE)


def test_no_snapshot_has_no_inventory(store: FactStore) -> None:
    inventory = measure_inventory(store, trend_updates=12)
    assert (inventory.since, inventory.kinds, inventory.arrived, inventory.left) == (None, [], [], [])


def test_kinds_are_counted_per_snapshot_from_the_validity_ranges(store: FactStore) -> None:
    store.record("c1", [package("httpx"), module("app")], [])
    store.record("c2", [package("httpx"), package("pydantic"), module("app")], [])
    store.record("c3", [package("httpx"), package("pydantic"), package("x"), module("app")], [])
    inventory = measure_inventory(store, trend_updates=2)
    assert inventory.kinds == [KindCount("package", 3, 1, [2, 3]), KindCount("module", 1, 0, [1, 1])]


def test_a_single_snapshot_has_no_change(store: FactStore) -> None:
    store.record("c1", [package("httpx")], [])
    assert measure_inventory(store, trend_updates=12).kinds == [KindCount("package", 1, None, [1])]


def test_packages_that_arrived_and_left_after_the_first_snapshot(store: FactStore) -> None:
    first, _ = store.record("c1", [package("httpx"), package("fastapi")], [])
    second, _ = store.record("c2", [package("httpx"), package("pydantic")], [])
    third, _ = store.record("c3", [package("httpx"), package("pydantic"), package("rich")], [])
    inventory = measure_inventory(store, trend_updates=12)
    assert inventory.since == first
    assert inventory.arrived == [Change("package:rich", third), Change("package:pydantic", second)]
    assert inventory.left == [Change("package:fastapi", second)]


def test_a_package_that_changed_keeps_its_first_arrival(store: FactStore) -> None:
    store.record("c1", [module("app")], [])
    second, _ = store.record("c2", [package("httpx")], [])
    store.record("c3", [Entity("package:httpx", EntityKind.PACKAGE, {"url": "x"})], [])
    inventory = measure_inventory(store, trend_updates=12)
    assert inventory.arrived == [Change("package:httpx", second)]
    assert inventory.left == []
