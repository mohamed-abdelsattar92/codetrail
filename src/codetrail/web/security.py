"""The page's security model (design section 7.4).

- Only requests whose Host is `127.0.0.1:<port>` or `localhost:<port>` are served (DNS rebinding).
- Every route but `/login` and `/static/` needs the session cookie, set once from a single-use login code that
  `codetrail serve` prints; the code expires.
- Every write (any method but GET and HEAD) needs an Origin equal to the served origin and the per-session token in
  the `X-Codetrail-Token` header (cross-site request forgery).
- A request a browser marks as coming from another site or another port (`Sec-Fetch-Site`) is refused.
- Every response, refusals included, carries the security headers.
Sessions live in memory and end with the process.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from http.cookies import SimpleCookie

from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "codetrail_session"
TOKEN_HEADER = "x-codetrail-token"  # noqa: S105 - a header name, not a secret
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
READ_METHODS = {"GET", "HEAD"}
OPEN_PATHS = ("/login", "/static/")
# Reads that return JSON for the page's script also need the token, as defence in depth (design 16.3).
TOKEN_READS = ("/search/results", "/bridge/answers")


class SessionState:
    """The one reader's session: a single-use login code, the session cookie's value and the write token."""

    def __init__(
        self, login_code_ttl_seconds: int, clock: Callable[[], float] = time.monotonic, session_minutes: int = 480
    ) -> None:
        self._ttl = login_code_ttl_seconds
        self._clock = clock
        # Browsers send a cookie to every port of a host, so a session must not outlive its use (design 7.4).
        self._session_expires = clock() + session_minutes * 60
        self._code: str | None = None
        self._code_expires = 0.0
        self.session_id = secrets.token_urlsafe(32)
        self.token = secrets.token_urlsafe(32)

    def issue_login_code(self) -> str:
        self._code = secrets.token_urlsafe(32)
        self._code_expires = self._clock() + self._ttl
        return self._code

    def redeem(self, code: str) -> bool:
        """True once for the issued code, before it expires; the code can't be used again."""
        expected = self._code
        if expected is None or not _equal(code, expected):
            return False
        self._code = None
        return self._clock() <= self._code_expires

    def is_session(self, cookie: str | None) -> bool:
        return cookie is not None and _equal(cookie, self.session_id) and self._clock() <= self._session_expires

    def is_token(self, token: str | None) -> bool:
        return token is not None and _equal(token, self.token)


def _equal(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def login_response(session: SessionState, code: str) -> Response:
    if not session.redeem(code):
        return PlainTextResponse("This sign-in link has expired or was already used. Run codetrail serve again.", 403)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, session.session_id, httponly=True, samesite="strict", path="/")
    return response


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, session: SessionState, port: int) -> None:
        self.app = app
        self.session = session
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            return  # no websockets
        refusal = self._refusal(scope)
        # Static files are revalidated on every load (cheap on 127.0.0.1), so after an upgrade the browser never mixes
        # last version's styles or script with this version's pages.
        extra = [(b"cache-control", b"no-cache")] if scope["path"].startswith("/static/") else []

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _ in SECURITY_HEADERS + extra}
                headers = [(name, value) for name, value in message.get("headers", []) if name not in names]
                message["headers"] = headers + SECURITY_HEADERS + extra
            await send(message)

        if refusal is not None:
            await refusal(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)

    def _refusal(self, scope: Scope) -> Response | None:
        headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"]}
        host = headers.get("host", "")
        if host not in self.hosts:
            return PlainTextResponse("This page is only served at 127.0.0.1 or localhost.", 400)
        path = scope["path"]
        if path.startswith("/static/"):
            return None
        # Browsers say where a request came from. Only the page itself or the address bar may reach Codetrail, so
        # another site, or a server on another 127.0.0.1 port (same-site), can't time or drive it (ASVS 3.5.8).
        if headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return PlainTextResponse("This request didn't come from Codetrail's page.", 403)
        if path == "/login":
            return None
        cookies = SimpleCookie(headers.get("cookie", ""))
        cookie = cookies[SESSION_COOKIE].value if SESSION_COOKIE in cookies else None
        if not self.session.is_session(cookie):
            return PlainTextResponse("Sign in with the link codetrail serve printed.", 403)
        if path in TOKEN_READS and not self.session.is_token(headers.get(TOKEN_HEADER)):
            return PlainTextResponse("This request didn't come from Codetrail's page.", 403)
        writes = scope["method"] not in READ_METHODS
        if writes and (
            headers.get("origin") != f"http://{host}" or not self.session.is_token(headers.get(TOKEN_HEADER))
        ):
            return PlainTextResponse("This request didn't come from Codetrail's page.", 403)
        return None
