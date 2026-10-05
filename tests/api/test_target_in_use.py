"""The page uses its target for each request, so no removal runs under a request, nor a request after a removal."""

from codetrail.config import Paths
from codetrail.lock import target_removal
from codetrail.remove import remove_target
from codetrail.web.security import SessionState
from tests.api.test_guide_pages import client_for
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
