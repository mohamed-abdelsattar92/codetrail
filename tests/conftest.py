"""Shared test settings."""

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_real_claude(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Only tests marked `live` may call the real Claude; anywhere else a call fails at once, costing nothing."""
    if request.node.get_closest_marker("live") is None:

        def refuse(*arguments: Any, **keywords: Any) -> Any:
            raise AssertionError("A test tried to call the real Claude; use FakeAssistant or mark the test live.")

        monkeypatch.setattr("codetrail.assistant.agent_sdk.query", refuse)
    yield
