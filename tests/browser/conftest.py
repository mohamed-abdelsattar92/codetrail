"""The page in a real browser (design section 16.7, ADR 0008).

The app runs as `codetrail serve` would, bound to 127.0.0.1 on a free port, over a fixture repository updated with
the fake assistant. Every test signs in through the real single-use `/login?code=` link; nothing in `src/` has a
test-only switch, and the security policy is never bypassed.
"""

import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import ConsoleMessage, Page

from codetrail.assistant import AnswerChunk, PageDraft, PageRequest, PlanDraft
from codetrail.assistant.estimate import CallEstimate, EstimateLine, UpdateEstimate
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.fixtures.repos import Commit, make_repository

PLAN = PlanDraft(
    [
        {"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []},
        {"id": "concepts/retries", "kind": "concept", "title": "Payment retries", "scope_paths": ["app"], "facts": []},
        {"id": "concepts/ledger", "kind": "concept", "title": "The ledger", "scope_paths": ["app"], "facts": []},
    ],
    paths=[
        {"id": "paths/start", "title": "Start here", "goal": "Learn the app.",
         "steps": ["areas/app", "concepts/retries", "concepts/ledger"]},
    ],
)  # fmt: skip
FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\n',
    "app/main.py": "from app import charges\n",
    "app/charges.py": "RETRIES = 3\n",
}
ESTIMATE = UpdateEstimate(
    [EstimateLine(CallEstimate("write", "claude_code", "claude-sonnet-5-5", 120_000, 6_000, 0.3, False), 2, 5)],
    {"claude_code": "Claude subscription (max)"}, [], 10.0, 5_000_000,
)  # fmt: skip


def writer(request: PageRequest) -> PageDraft:
    body = (
        f"## How {request.title} works\n\nA failed charge is retried with a backoff delay.\n\n"
        "> [!inferred]\n> The delay grows with each attempt.\n\n## Where it lives\n\nIn `app/charges.py`.\n"
    )
    checks = [{"id": "q1", "question": "Why retry?", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}]
    return PageDraft(body, checks, ["app/main.py"])


@dataclass
class Site:
    url: str
    session: SessionState
    claude: FakeAssistant
    decisions: list[bool] = field(default_factory=list)

    def sign_in(self, page: Page, path: str = "/") -> None:
        page.goto(f"{self.url}/login?code={self.session.issue_login_code()}")
        if path != "/":
            page.goto(f"{self.url}{path}")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def site(tmp_path: Path) -> Iterator[Site]:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "shop", make_repository(tmp_path / "target", [FILES]), "develop")
    # Playwright keeps an event loop running on this thread; the update runs its own, so it gets a thread.
    updating = threading.Thread(target=run_update, args=(paths, "shop"),
                                kwargs={"claude": FakeAssistant(plans=[PLAN], page_writer=writer)})  # fmt: skip
    updating.start()
    updating.join()
    port = free_port()
    settings = GlobalConfig.model_validate({"server": {"port": port, "update_cooldown_seconds": 0}})
    answers = [
        [AnswerChunk(f"Charges retry **three** times ({number})."), AnswerChunk(done=True)] for number in range(5)
    ]
    claude = FakeAssistant(answers=list(answers))
    session = SessionState(60)
    decisions: list[bool] = []

    def updater(confirm: Callable[[UpdateEstimate], bool]) -> None:
        decisions.append(confirm(ESTIMATE))

    app = create_app(paths, "shop", session, settings, updater=updater, assistant_for=lambda: claude)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    # Fail closed: if another process took the port, nothing must be sent to it.
    assert server.started and thread.is_alive(), "The test server didn't start."
    yield Site(f"http://127.0.0.1:{port}", session, claude, decisions)
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def errors(page: Page) -> list[str]:
    """Console errors, including the browser's reports of anything the security policy blocked."""
    found: list[str] = []

    def collect(message: ConsoleMessage) -> None:
        if message.type == "error":
            found.append(message.text)

    page.on("console", collect)
    page.on("pageerror", lambda error: found.append(str(error)))
    return found
