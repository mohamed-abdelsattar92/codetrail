"""`codetrail serve` prepares a locked-down app on 127.0.0.1 and a single-use sign-in link."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.config import Paths, write_target
from codetrail.server import prepare_server
from tests.fixtures.repos import make_repository


def test_prepare_server_gives_a_loopback_url_with_a_working_code(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    write_target(paths, "t", make_repository(tmp_path / "target", [{"a.md": "a\n"}]), "develop")
    server = prepare_server(paths, "t")
    assert server.host == "127.0.0.1"
    assert server.url.startswith("http://127.0.0.1:8765/login?code=")
    client = TestClient(server.app, base_url="http://127.0.0.1:8765", follow_redirects=False)
    assert client.get(server.url.removeprefix("http://127.0.0.1:8765")).status_code == 303
    assert client.get("/").status_code == 200


def test_serve_refuses_an_unknown_target(tmp_path: Path) -> None:
    from codetrail.errors import CodetrailError

    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    with pytest.raises(CodetrailError):
        prepare_server(paths, "missing")
