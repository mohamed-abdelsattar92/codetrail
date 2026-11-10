"""The real hosting programs, read-only (`just test-live`; CI skips it).

Each runs only where it is installed. The test checks that the calls return and prints and records no names.
"""

import shutil

import pytest

from codetrail.config import GlobalConfig
from codetrail.hosting.services import services_for

pytestmark = pytest.mark.live
PROGRAMS = {"github": "gh", "azure_devops": "az", "gitlab": "glab"}


@pytest.mark.parametrize("service", list(PROGRAMS))
def test_installed_programs_answer_read_only(service: str) -> None:
    if shutil.which(PROGRAMS[service]) is None:
        pytest.skip(f"{PROGRAMS[service]} isn't installed here.")
    adapter = services_for(GlobalConfig())[service]  # type: ignore[index]
    status = adapter.status()
    assert status.installed
    if not status.signed_in:
        pytest.skip(f"{PROGRAMS[service]} isn't signed in here ({status.fix}).")
    sources = adapter.sources()
    if sources:
        assert isinstance(adapter.repositories(sources[0].name), list)
