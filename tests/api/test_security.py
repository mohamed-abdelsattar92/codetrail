"""The page's security model: Host, session, Origin and token, and headers (design section 7.4)."""

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from codetrail.web.security import SESSION_COOKIE, TOKEN_HEADER, SecurityMiddleware, SessionState, login_response

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def session(clock: Clock) -> SessionState:
    return SessionState(login_code_ttl_seconds=60, clock=clock)


@pytest.fixture
def client(session: SessionState) -> TestClient:
    app = FastAPI()

    @app.get("/")
    def home() -> PlainTextResponse:
        return PlainTextResponse("home")

    @app.post("/echo")
    def echo() -> PlainTextResponse:
        return PlainTextResponse("written")

    @app.get("/login")
    def login(code: str = "") -> object:
        return login_response(session, code)

    @app.get("/static/page.js")
    def script() -> PlainTextResponse:
        return PlainTextResponse("// script")

    app.add_middleware(SecurityMiddleware, session=session, port=PORT)
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def signed_in(client: TestClient, session: SessionState) -> TestClient:
    response = client.get(f"/login?code={session.issue_login_code()}")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    return client


HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-resource-policy": "same-origin",
}


def assert_security_headers(response: object) -> None:
    for name, value in HEADERS.items():
        assert response.headers[name] == value  # type: ignore[attr-defined]


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example:8765", "127.0.0.1:9999", "localhost", ""])
def test_other_hosts_are_refused_even_when_signed_in(client: TestClient, session: SessionState, host: str) -> None:
    signed_in(client, session)
    response = client.get("/", headers={"host": host})
    assert response.status_code == 400
    assert_security_headers(response)


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
def test_the_served_hosts_are_accepted(client: TestClient, session: SessionState, host: str) -> None:
    signed_in(client, session)
    assert client.get("/", headers={"host": host}).status_code == 200


def test_pages_need_the_session(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 403
    assert_security_headers(response)


def test_a_signed_in_page_carries_the_headers(client: TestClient, session: SessionState) -> None:
    response = signed_in(client, session).get("/")
    assert response.status_code == 200
    assert_security_headers(response)


def test_the_cookie_is_strict(client: TestClient, session: SessionState) -> None:
    response = client.get(f"/login?code={session.issue_login_code()}")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie


def test_a_login_code_works_once(client: TestClient, session: SessionState) -> None:
    code = session.issue_login_code()
    assert client.get(f"/login?code={code}").status_code == 303
    client.cookies.clear()
    assert client.get(f"/login?code={code}").status_code == 403


def test_an_expired_login_code_is_refused(client: TestClient, session: SessionState, clock: Clock) -> None:
    code = session.issue_login_code()
    clock.now += 61
    assert client.get(f"/login?code={code}").status_code == 403


@pytest.mark.parametrize("code", ["", "wrong", "x" * 500])
def test_wrong_login_codes_are_refused(client: TestClient, session: SessionState, code: str) -> None:
    session.issue_login_code()
    assert client.get(f"/login?code={code}").status_code == 403


def test_writes_need_origin_and_token(client: TestClient, session: SessionState) -> None:
    signed_in(client, session)
    assert client.post("/echo").status_code == 403
    assert client.post("/echo", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/echo", headers={"origin": ORIGIN}).status_code == 403
    assert client.post("/echo", headers={"origin": ORIGIN, TOKEN_HEADER: "wrong"}).status_code == 403
    assert client.post("/echo", headers={"origin": "null", TOKEN_HEADER: session.token}).status_code == 403
    response = client.post("/echo", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
    assert response.status_code == 200


def test_writes_need_the_session_too(client: TestClient, session: SessionState) -> None:
    response = client.post("/echo", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
    assert response.status_code == 403


def test_static_files_need_no_session_but_carry_the_headers(client: TestClient) -> None:
    response = client.get("/static/page.js")
    assert response.status_code == 200
    assert_security_headers(response)
    # Revalidated on every load, so an upgraded Codetrail never shows a page with last version's styles or script.
    assert response.headers["cache-control"] == "no-cache"


def test_a_forged_cookie_is_refused(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "guess")
    assert client.get("/").status_code == 403


def test_tokens_are_long_and_random() -> None:
    first, second = SessionState(60), SessionState(60)
    assert first.token != second.token
    assert len(first.token) >= 43  # 32 random bytes


def test_a_session_ends_after_its_lifetime(clock: Clock) -> None:
    session = SessionState(60, clock=clock, session_minutes=10)
    assert session.is_session(session.session_id)
    clock.now += 10 * 60 + 1
    assert not session.is_session(session.session_id)


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_requests_from_other_sites_and_ports_are_refused(client: TestClient, session: SessionState, site: str) -> None:
    signed_in(client, session)
    for path in ("/", f"/login?code={session.issue_login_code()}"):
        response = client.get(path, headers={"sec-fetch-site": site})
        assert response.status_code == 403
        assert_security_headers(response)


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_requests_from_the_page_or_the_address_bar_are_served(
    client: TestClient, session: SessionState, site: str | None
) -> None:
    signed_in(client, session)
    headers = {"sec-fetch-site": site} if site else {}
    assert client.get("/", headers=headers).status_code == 200
