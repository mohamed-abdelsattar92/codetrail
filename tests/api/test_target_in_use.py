"""The page uses its target for each request, so no removal runs under a request, nor a request after a removal."""

from fastapi.testclient import TestClient

from codetrail.config import GlobalConfig, Paths
from codetrail.lock import target_removal
from codetrail.remove import remove_target
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.api.test_guide_pages import ORIGIN, client_for
from tests.api.test_guide_pages import paths as paths  # the fixture


def test_a_request_during_a_removal_is_refused(paths: Paths) -> None:
    client = client_for(paths, SessionState(60))
    with target_removal(paths, "t"):
        response = client.get("/")
    assert response.status_code == 409
    assert "being removed" in response.text


def test_after_a_removal_a_request_recreates_nothing(paths: Paths) -> None:
    """serve may hold its lock on a settings file an editor has replaced; each request locks the current one."""
    client = client_for(paths, SessionState(60))
    assert remove_target(paths, "t", lambda found: True)

    response = client.get("/")

    assert response.status_code == 410
    assert "No target named 't'" in response.text
    assert not paths.target_data("t").exists()


def test_sign_in_and_static_files_never_touch_the_target(paths: Paths) -> None:
    """They need no session, so they mustn't reveal anything about the target, even while it's being removed."""
    client = TestClient(create_app(paths, "t", SessionState(60), GlobalConfig()), base_url=ORIGIN)
    with target_removal(paths, "t"):
        assert client.get("/static/css/base.css").status_code == 200
        login = client.get("/login?code=wrong")
    assert login.status_code == 403
    assert "removed" not in login.text
