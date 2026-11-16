"""The page's own links, under the prefix it's served at (design section 24.7).

`codetrail serve <target>` serves a target's page at `/`; the team page mounts each target's page under
`/repos/<target>/`. Every link the page builds goes through `site_path`, which adds the prefix of the request being
served. The shared static files stay at the root, so their links never go through it.
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

BASE_PATH: ContextVar[str] = ContextVar("BASE_PATH", default="")


def site_path(path: str) -> str:
    """`path`, which starts with `/` (or is empty, for the prefix alone), under the page's prefix."""
    return BASE_PATH.get() + path


class BasePathMiddleware:
    """Sets the prefix for each request: the path the page was mounted at, which Starlette's `Mount` passes as the
    request's `root_path` (empty when the page is served at `/`)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = BASE_PATH.set(scope.get("root_path", ""))
        try:
            await self.app(scope, receive, send)
        finally:
            BASE_PATH.reset(token)
