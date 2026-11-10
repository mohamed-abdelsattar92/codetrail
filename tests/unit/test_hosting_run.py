"""The read-only runner for the reader's hosting programs (design section 24.3)."""

import json
from pathlib import Path

import pytest

from codetrail.config import HostingSettings
from codetrail.hosting import HostingError
from codetrail.hosting.run import READS, TOKEN_VARIABLES, hosting_environment, is_read, run_read
from tests.fixtures.programs import make_program

# The reviewer's checklist: every command prefix an adapter may run, written by hand. READS must hold nothing else.
KNOWN_READS = {
    "github": {
        ("auth", "status", "--json", "hosts"),
        ("repo", "list"),
        ("api", "--method", "GET"),
    },
    "azure_devops": {
        ("account", "show", "-o", "json"),
        ("devops", "project", "list"),
        ("repos", "list"),
        ("repos", "ref", "list"),
    },
    "gitlab": {
        ("auth", "status", "--output", "json"),
        ("api", "--method", "GET"),
    },
}
SETTINGS = HostingSettings()


def test_token_variables_never_reach_the_program() -> None:
    environ = {name: "secret" for name in TOKEN_VARIABLES} | {
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://proxy.example:3128",
        "SSL_CERT_FILE": "/etc/ssl/corp.pem",
        "LC_ALL": "C",
        "GH_CONFIG_DIR": "/home/reader/.config/gh",
        "AWS_SECRET_ACCESS_KEY": "secret",
    }
    environment = hosting_environment(environ)
    assert not TOKEN_VARIABLES & environment.keys()
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert environment["SSL_CERT_FILE"] == "/etc/ssl/corp.pem"
    assert environment["LC_ALL"] == "C"
    assert environment["GH_CONFIG_DIR"] == "/home/reader/.config/gh"


def test_the_token_variables_include_the_ones_the_spike_found() -> None:
    assert {"GH_TOKEN", "GITHUB_TOKEN", "AZURE_DEVOPS_EXT_PAT", "GITLAB_TOKEN", "CI_JOB_TOKEN"} <= TOKEN_VARIABLES
    assert {"OAUTH_TOKEN", "GITLAB_CLIENT_ID", "GLAB_CLIENT_CERT", "CLIENT_CERT"} <= TOKEN_VARIABLES
    assert {"GLAB_CLIENT_KEY", "CLIENT_KEY", "GLAB_CLIENT_CERT_PASSWORD"} <= TOKEN_VARIABLES


def test_relative_path_entries_are_dropped() -> None:
    assert hosting_environment({"PATH": "/usr/bin::bin:./x:/opt/tools"})["PATH"] == "/usr/bin:/opt/tools"


def test_a_command_outside_the_read_list_is_refused_before_it_runs(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-gh", ["{}"])
    for arguments in (
        ["repo", "delete", "x"],
        ["repo", "create", "x"],
        ["auth", "setup-git"],
        ["api", "--method", "POST", "repos/acme-org/web-shop/issues"],
        ["api", "repos/acme-org/web-shop"],
        ["api", "--method", "GET", "x", "-X", "DELETE"],
        ["api", "--method", "GET", "x", "-fname=y"],
        ["api", "--method", "GET", "x", "--input", "file"],
        [],
    ):
        with pytest.raises(HostingError, match="isn't a read"):
            run_read("github", str(program.path), arguments, SETTINGS)
    assert not program.record_file.exists()


def test_every_read_prefix_is_a_known_read() -> None:
    assert {service: set(prefixes) for service, prefixes in READS.items()} == KNOWN_READS
    for prefixes in READS.values():
        for prefix in prefixes:
            if prefix[0] == "api":
                assert prefix[1:3] == ("--method", "GET")


def test_is_read_matches_by_prefix() -> None:
    assert is_read("github", ["repo", "list", "acme-org", "--json", "id", "--limit", "5"])
    assert is_read("gitlab", ["api", "--method", "GET", "groups/12/projects?include_subgroups=true", "--paginate"])
    assert not is_read("gitlab", ["repo", "list"])


def test_a_read_runs_from_the_root_with_the_allowlisted_environment(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-gh", ['{"hosts": {}}'])
    environ = {"PATH": "/usr/bin:/bin", "GH_TOKEN": "secret", "HOME": "/home/reader"}
    output = run_read("github", str(program.path), ["auth", "status", "--json", "hosts"], SETTINGS, environ)
    assert json.loads(output) == {"hosts": {}}
    record = program.record
    assert record["argv"] == ["auth", "status", "--json", "hosts"]
    assert Path(record["cwd"]).resolve() == Path("/").resolve()
    assert "GH_TOKEN" not in record["env"]
    assert record["env"]["HOME"] == "/home/reader"


def test_output_over_the_limit_is_refused(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-gh", ["x" * 1000])
    small = SETTINGS.model_copy(update={"max_output_bytes": 500})
    with pytest.raises(HostingError, match="more than 500 bytes"):
        run_read("github", str(program.path), ["repo", "list", "acme-org"], small, {"PATH": "/usr/bin:/bin"})


def test_a_failing_program_names_itself_and_its_last_line(tmp_path: Path) -> None:
    stderr = "warning: first\nHTTP 401: Bad credentials (acme-org-secret-repo)\n"
    program = make_program(tmp_path, "fake-gh", [], exit_code=1, stderr=stderr)
    with pytest.raises(HostingError) as raised:
        run_read("github", str(program.path), ["repo", "list", "acme-org"], SETTINGS, {"PATH": "/usr/bin:/bin"})
    message = str(raised.value)
    assert message.startswith("fake-gh stopped with exit code 1")
    assert "Bad credentials" in message
    assert "warning: first" not in message
    assert "repo list" not in message


def test_a_missing_program_is_a_hosting_error(tmp_path: Path) -> None:
    with pytest.raises(HostingError, match="isn't installed"):
        run_read("github", "gh-not-here", ["repo", "list", "acme-org"], SETTINGS, {"PATH": str(tmp_path)})


def test_azure_names_with_spaces_are_one_argument(tmp_path: Path) -> None:
    script = tmp_path / "bin" / "fake-az"
    script.parent.mkdir()
    script.write_text('#!/bin/sh\nprintf \'["%s", "%s"]\' "$6" "$4"\n')
    script.chmod(0o755)
    arguments = ["repos", "list", "--organization", "https://dev.azure.com/contoso", "--project", "Payments API #2"]
    output = run_read("azure_devops", str(script), arguments, SETTINGS, {"PATH": "/usr/bin:/bin"})
    assert json.loads(output)[0] == "Payments API #2"
