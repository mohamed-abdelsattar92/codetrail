"""The GitHub, Azure DevOps and GitLab adapters, run against fake programs (design section 24.3)."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from codetrail.config import GlobalConfig, HostingSettings
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, Source
from codetrail.hosting.azure_devops import AzureDevOps
from codetrail.hosting.github import GitHub
from codetrail.hosting.gitlab import GitLab
from codetrail.hosting.run import is_read
from codetrail.hosting.services import services_for
from tests.fixtures.programs import FakeProgram, make_program

FIXTURES = Path(__file__).parent.parent / "fixtures" / "hosting"
ORGANIZATION_URL = "https://dev.azure.com/contoso"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def program(tmp_path: Path, name: str, output: str, exit_code: int = 0) -> FakeProgram:
    return make_program(tmp_path, name, [output], exit_code=exit_code)


def settings_for(field: str, fake: FakeProgram | str, **limits: int) -> HostingSettings:
    program_path = str(fake.path) if isinstance(fake, FakeProgram) else fake
    return HostingSettings().model_copy(update={field: program_path, **limits})


def repository_of(service: Any, source: str, identifier: str = "1", name: str = "web-shop") -> HostedRepository:
    return HostedRepository(
        service, source, identifier, name, "main", False, False, None, None, "https://x", "git@x:y", "https://x"
    )


def read_only(service: Any, fake: FakeProgram) -> None:
    assert is_read(service, fake.record["argv"])


# GitHub

GITHUB_SIGNED_IN = json.dumps(
    {"hosts": {"github.com": [{"state": "success", "active": True, "host": "github.com", "login": "acme-user"}]}}
)


def test_github_parses_repositories(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-gh", fixture("github_repos.json"))
    found = GitHub(settings_for("gh", fake)).repositories("acme-org")
    assert fake.record["argv"][:3] == ["repo", "list", "acme-org"]
    assert fake.record["argv"][3:] == [
        "--json",
        "id,name,defaultBranchRef,isArchived,isEmpty,isFork,isTemplate,pushedAt,diskUsage,url,sshUrl",
        "--limit",
        "1000",
    ]
    read_only("github", fake)
    assert found[0] == HostedRepository(
        service="github",
        source="acme-org",
        id="R_kgDOAAAAAQ",
        name="web-shop",
        default_branch="main",
        archived=False,
        disabled=False,
        pushed_at=datetime(2026, 11, 2, 9, 15, tzinfo=UTC),
        size_kb=5120,
        https_url="https://github.com/acme-org/web-shop.git",
        ssh_url="git@github.com:acme-org/web-shop.git",
        web_url="https://github.com/acme-org/web-shop",
    )
    assert found[1].archived and found[1].default_branch == "master" and len(found) == 2


def test_github_an_empty_repository_has_no_default_branch(tmp_path: Path) -> None:
    records = json.loads(fixture("github_repos.json"))
    records[0]["isEmpty"] = True
    records[1]["defaultBranchRef"] = None
    found = GitHub(settings_for("gh", program(tmp_path, "fake-gh", json.dumps(records)))).repositories("acme-org")
    assert [repository.default_branch for repository in found] == [None, None]


def test_github_sources_are_the_account_then_its_organizations(tmp_path: Path) -> None:
    calls = tmp_path / "calls.jsonl"
    script = tmp_path / "fake-gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(calls)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print({GITHUB_SIGNED_IN!r} if sys.argv[1] == 'auth' else {fixture('github_orgs.json')!r})\n"
    )
    script.chmod(0o755)
    sources = GitHub(settings_for("gh", str(script))).sources()
    assert sources == [Source("github", "acme-user", None), Source("github", "acme-org", None)]
    argument_lists = [json.loads(line) for line in calls.read_text().splitlines()]
    assert argument_lists == [
        ["auth", "status", "--json", "hosts"],
        ["api", "--method", "GET", "user/orgs", "--paginate"],
    ]
    assert all(is_read("github", arguments) for arguments in argument_lists)


def test_github_status_signed_in(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-gh", GITHUB_SIGNED_IN)
    status = GitHub(settings_for("gh", fake)).status()
    assert (status.installed, status.signed_in, status.account, status.fix) == (True, True, "acme-user", None)
    assert fake.record["argv"] == ["auth", "status", "--json", "hosts"]
    read_only("github", fake)


def test_github_status_signed_out_names_the_fix(tmp_path: Path) -> None:
    for output, code in ((json.dumps({"hosts": {}}), 0), ("", 1)):
        status = GitHub(settings_for("gh", program(tmp_path, "fake-gh", output, code))).status()
        assert (status.installed, status.signed_in, status.account) == (True, False, None)
        assert status.fix == "gh auth login"


def test_github_missing_program_is_not_installed(tmp_path: Path) -> None:
    status = GitHub(settings_for("gh", str(tmp_path / "not-there"))).status()
    assert (status.installed, status.signed_in) == (False, False)
    assert status.fix is not None and "cli.github.com" in status.fix


def test_github_drops_a_repository_with_an_invalid_url(tmp_path: Path, caplog: Any) -> None:
    records = json.loads(fixture("github_repos.json"))
    records[0]["url"] = "https://evil.example/acme-org/web-shop"
    fake = program(tmp_path, "fake-gh", json.dumps(records))
    found = GitHub(settings_for("gh", fake)).repositories("acme-org")
    assert [repository.name for repository in found] == ["payments-api"]
    assert "web-shop" not in caplog.text and "R_kgDOAAAAAQ" in caplog.text


def test_github_drops_a_repository_with_an_invalid_name(tmp_path: Path) -> None:
    records = json.loads(fixture("github_repos.json"))
    records[0]["name"] = "--web shop"
    found = GitHub(settings_for("gh", program(tmp_path, "fake-gh", json.dumps(records)))).repositories("acme-org")
    assert [repository.name for repository in found] == ["payments-api"]


def test_github_stops_at_the_repository_limit(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-gh", fixture("github_repos.json"))
    found = GitHub(settings_for("gh", fake, max_repositories_per_source=1)).repositories("acme-org")
    assert [repository.name for repository in found] == ["web-shop"]
    assert fake.record["argv"][-2:] == ["--limit", "1"]


def test_github_branches(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-gh", "main\nfeature/checkout\n")
    branches = GitHub(settings_for("gh", fake)).branches(repository_of("github", "acme-org"))
    assert branches == ["main", "feature/checkout"]
    assert fake.record["argv"] == [
        "api",
        "--method",
        "GET",
        "repos/acme-org/web-shop/branches",
        "--paginate",
        "--jq",
        ".[].name",
    ]
    read_only("github", fake)


def test_github_refuses_a_source_that_is_not_a_name(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-gh", "[]")
    with pytest.raises(CodetrailError, match="valid source"):
        GitHub(settings_for("gh", fake)).repositories("--help")
    assert not fake.record_file.exists()


# Azure DevOps


def azure_repositories() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = json.loads(fixture("azure_repos.json"))
    records[0]["name"] = "Payments API 2"  # '#' isn't accepted in a repository name (names.py)
    return records


def test_azure_parses_repositories(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", json.dumps(azure_repositories()))
    found = AzureDevOps(settings_for("az", fake)).repositories("contoso/Platform")
    assert fake.record["argv"] == [
        "repos",
        "list",
        "--organization",
        ORGANIZATION_URL,
        "--project",
        "Platform",
        "-o",
        "json",
    ]
    read_only("azure_devops", fake)
    assert found[0] == HostedRepository(
        service="azure_devops",
        source="contoso/Platform",
        id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        name="Payments API 2",
        default_branch="main",
        archived=False,
        disabled=False,
        pushed_at=None,
        size_kb=2048,
        https_url="https://dev.azure.com/contoso/Platform/_git/Payments%20API%20%232",
        ssh_url="git@ssh.dev.azure.com:v3/contoso/Platform/Payments%20API%20%232",
        web_url="https://dev.azure.com/contoso/Platform/_git/Payments%20API%20%232",
    )
    assert found[1].disabled and found[1].default_branch is None and found[1].name == "web-shop"


def test_azure_drops_a_repository_whose_name_is_not_accepted(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", fixture("azure_repos.json"))  # the first is "Payments API #2"
    found = AzureDevOps(settings_for("az", fake)).repositories("contoso/Platform")
    assert [repository.name for repository in found] == ["web-shop"]


def test_azure_sources_are_the_organizations_projects(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", fixture("azure_projects.json"))
    service = AzureDevOps(settings_for("az", fake))
    assert service.sources("contoso") == [
        Source("azure_devops", "contoso/Platform", None),
        Source("azure_devops", "contoso/Legacy Apps", None),
    ]
    assert fake.record["argv"] == ["devops", "project", "list", "--organization", ORGANIZATION_URL, "-o", "json"]
    read_only("azure_devops", fake)
    fake.record_file.unlink()
    assert service.sources() == [] and not fake.record_file.exists()


def test_azure_sources_accept_a_bare_list(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", json.dumps([{"name": "Platform"}]))
    sources = AzureDevOps(settings_for("az", fake)).sources("contoso")
    assert sources == [Source("azure_devops", "contoso/Platform", None)]


def test_azure_status_signed_in(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", json.dumps({"user": {"name": "person@example.com"}, "name": "Subscription"}))
    status = AzureDevOps(settings_for("az", fake)).status()
    assert (status.installed, status.signed_in, status.account, status.fix) == (True, True, "person@example.com", None)
    assert fake.record["argv"] == ["account", "show", "-o", "json"]
    read_only("azure_devops", fake)


def test_azure_status_signed_out_names_the_fix(tmp_path: Path) -> None:
    status = AzureDevOps(settings_for("az", program(tmp_path, "fake-az", "", 1))).status()
    assert (status.installed, status.signed_in, status.account, status.fix) == (True, False, None, "az login")


def test_azure_missing_program_is_not_installed(tmp_path: Path) -> None:
    status = AzureDevOps(settings_for("az", str(tmp_path / "not-there"))).status()
    assert (status.installed, status.signed_in) == (False, False)
    assert status.fix is not None and "az extension add --name azure-devops" in status.fix


def test_azure_drops_a_repository_with_an_invalid_url(tmp_path: Path) -> None:
    records = azure_repositories()
    records[1]["sshUrl"] = "git@evil.example:v3/contoso/Platform/web-shop"
    found = AzureDevOps(settings_for("az", program(tmp_path, "fake-az", json.dumps(records)))).repositories(
        "contoso/Platform"
    )
    assert [repository.name for repository in found] == ["Payments API 2"]


def test_azure_stops_at_the_repository_limit(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", json.dumps(azure_repositories()))
    found = AzureDevOps(settings_for("az", fake, max_repositories_per_source=1)).repositories("contoso/Platform")
    assert len(found) == 1


def test_azure_branches(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", fixture("azure_branches.json"))
    repository = repository_of("azure_devops", "contoso/Platform", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert AzureDevOps(settings_for("az", fake)).branches(repository) == ["main", "feature/checkout"]
    assert fake.record["argv"] == [
        "repos",
        "ref",
        "list",
        "--organization",
        ORGANIZATION_URL,
        "--project",
        "Platform",
        "--repository",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "--filter",
        "heads/",
        "-o",
        "json",
    ]
    read_only("azure_devops", fake)


# GitLab


def test_gitlab_parses_repositories(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", fixture("gitlab_projects.json"))
    found = GitLab(settings_for("glab", fake)).repositories("example-group")
    assert fake.record["argv"] == [
        "api",
        "--method",
        "GET",
        "groups/example-group/projects?include_subgroups=true&archived=false",
        "--paginate",
    ]
    read_only("gitlab", fake)
    assert found[0] == HostedRepository(
        service="gitlab",
        source="example-group",
        id="9001",
        name="web-shop",
        default_branch="main",
        archived=False,
        disabled=False,
        pushed_at=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        size_kb=None,
        https_url="https://gitlab.com/example-group/web-shop.git",
        ssh_url="git@gitlab.com:example-group/web-shop.git",
        web_url="https://gitlab.com/example-group/web-shop",
    )
    assert found[1].archived and found[1].name == "payments-api"


def test_gitlab_encodes_a_subgroup_path(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", "[]")
    GitLab(settings_for("glab", fake)).repositories("example-group/tools")
    assert fake.record["argv"][3].startswith("groups/example-group%2Ftools/projects?")


def test_gitlab_disabled_and_empty_repositories(tmp_path: Path) -> None:
    records = json.loads(fixture("gitlab_projects.json"))
    records[0]["repository_access_level"] = "disabled"
    records[1]["empty_repo"] = True
    found = GitLab(settings_for("glab", program(tmp_path, "fake-glab", json.dumps(records)))).repositories("g")
    assert [(r.disabled, r.default_branch) for r in found] == [(True, "main"), (False, None)]


def test_gitlab_reads_concatenated_pages(tmp_path: Path) -> None:
    pages = "\n".join(json.dumps([record]) for record in json.loads(fixture("gitlab_projects.json")))
    found = GitLab(settings_for("glab", program(tmp_path, "fake-glab", pages))).repositories("example-group")
    assert [repository.id for repository in found] == ["9001", "9002"]


def test_gitlab_sources_are_the_groups(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", fixture("gitlab_groups.json"))
    sources = GitLab(settings_for("glab", fake)).sources()
    assert sources == [Source("gitlab", "example-group", None), Source("gitlab", "example-group/tools", None)]
    assert fake.record["argv"] == ["api", "--method", "GET", "groups?min_access_level=10", "--paginate"]
    read_only("gitlab", fake)


def test_gitlab_status_signed_in_has_no_account(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", json.dumps({"hosts": {"gitlab.com": [{"state": "ok"}]}}))
    status = GitLab(settings_for("glab", fake)).status()
    assert (status.installed, status.signed_in, status.account, status.fix) == (True, True, None, None)
    assert fake.record["argv"] == ["auth", "status", "--output", "json"]
    read_only("gitlab", fake)


def test_gitlab_status_signed_out_names_the_fix(tmp_path: Path) -> None:
    for output, code in (("", 1), (json.dumps({"hosts": {}}), 0)):
        status = GitLab(settings_for("glab", program(tmp_path, "fake-glab", output, code))).status()
        assert (status.installed, status.signed_in, status.fix) == (True, False, "glab auth login")


def test_gitlab_missing_program_is_not_installed(tmp_path: Path) -> None:
    status = GitLab(settings_for("glab", str(tmp_path / "not-there"))).status()
    assert (status.installed, status.signed_in) == (False, False)
    assert status.fix is not None and "glab" in status.fix


def test_gitlab_drops_a_repository_with_an_invalid_url(tmp_path: Path) -> None:
    records = json.loads(fixture("gitlab_projects.json"))
    records[1]["http_url_to_repo"] = "http://gitlab.com/example-group/tools/payments-api.git"
    found = GitLab(settings_for("glab", program(tmp_path, "fake-glab", json.dumps(records)))).repositories("g")
    assert [repository.name for repository in found] == ["web-shop"]


def test_gitlab_stops_at_the_repository_limit(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", fixture("gitlab_projects.json"))
    found = GitLab(settings_for("glab", fake, max_repositories_per_source=1)).repositories("example-group")
    assert [repository.id for repository in found] == ["9001"]


def test_gitlab_branches(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", fixture("gitlab_branches.json"))
    branches = GitLab(settings_for("glab", fake)).branches(repository_of("gitlab", "example-group", "9001"))
    assert branches == ["main", "feature/checkout"]
    assert fake.record["argv"] == ["api", "--method", "GET", "projects/9001/repository/branches", "--paginate"]
    read_only("gitlab", fake)


def test_services_for_builds_one_adapter_per_service() -> None:
    services = services_for(GlobalConfig())
    assert set(services) == {"github", "azure_devops", "gitlab"}
    assert all(service.service == name for name, service in services.items())


# Fix round 1: ids, branches, web URLs and the log


def test_azure_branches_refuse_an_id_that_could_be_a_flag(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-az", "[]")
    for identifier in ("--help", "-x", "a b", ""):
        repository = repository_of("azure_devops", "contoso/Platform", identifier)
        with pytest.raises(CodetrailError, match="id"):
            AzureDevOps(settings_for("az", fake)).branches(repository)
    assert not fake.record_file.exists()


def test_gitlab_branches_refuse_a_non_ascii_digit_id(tmp_path: Path) -> None:
    fake = program(tmp_path, "fake-glab", "[]")
    with pytest.raises(CodetrailError, match="number"):
        GitLab(settings_for("glab", fake)).branches(repository_of("gitlab", "example-group", "١٢"))
    assert not fake.record_file.exists()


def test_github_sources_skip_an_invalid_login_without_naming_it(tmp_path: Path, caplog: Any) -> None:
    script = tmp_path / "fake-gh"
    orgs = json.dumps([{"login": "--secret-org"}, {"login": "acme-org"}])
    script.write_text(
        f"#!{sys.executable}\nimport sys\nprint({GITHUB_SIGNED_IN!r} if sys.argv[1] == 'auth' else {orgs!r})\n"
    )
    script.chmod(0o755)
    sources = GitHub(settings_for("gh", str(script))).sources()
    assert [source.name for source in sources] == ["acme-user", "acme-org"]
    assert "secret-org" not in caplog.text and caplog.text


def mutate(records: list[dict[str, Any]], key: str, value: Any) -> str:
    records[0][key] = value
    return json.dumps(records)


def github_case(tmp_path: Path, key: str, value: Any) -> list[HostedRepository]:
    output = mutate(json.loads(fixture("github_repos.json")), key, value)
    return GitHub(settings_for("gh", program(tmp_path, "fake-gh", output))).repositories("acme-org")


def azure_case(tmp_path: Path, key: str, value: Any) -> list[HostedRepository]:
    output = mutate(azure_repositories(), key, value)
    return AzureDevOps(settings_for("az", program(tmp_path, "fake-az", output))).repositories("contoso/Platform")


def gitlab_case(tmp_path: Path, key: str, value: Any) -> list[HostedRepository]:
    output = mutate(json.loads(fixture("gitlab_projects.json")), key, value)
    return GitLab(settings_for("glab", program(tmp_path, "fake-glab", output))).repositories("example-group")


BAD_VALUES = [
    (github_case, "id", "--flag"),
    (github_case, "defaultBranchRef", {"name": "--upload-pack=x"}),
    (github_case, "url", "https://evil.example/acme-org/web-shop"),
    (azure_case, "id", "--flag"),
    (azure_case, "defaultBranch", "refs/heads/--upload-pack=x"),
    (azure_case, "webUrl", "https://evil.example/contoso/Platform/_git/x"),
    (gitlab_case, "id", "-1"),
    (gitlab_case, "default_branch", "--upload-pack=x"),
    (gitlab_case, "web_url", "https://evil.example/example-group/web-shop"),
    (gitlab_case, "web_url", "http://gitlab.com/example-group/web-shop"),
]


@pytest.mark.parametrize(("case", "key", "value"), BAD_VALUES)
def test_a_repository_with_a_bad_id_branch_or_web_url_is_left_out(
    tmp_path: Path, caplog: Any, case: Any, key: str, value: Any
) -> None:
    found = case(tmp_path, key, value)
    assert len(found) == 1
    assert "web-shop" not in caplog.text and "Payments API" not in caplog.text and caplog.text


def test_a_long_id_is_cut_in_the_log(tmp_path: Path, caplog: Any) -> None:
    found = github_case(tmp_path, "id", "-" + "x" * 500)
    assert len(found) == 1
    assert "x" * 100 not in caplog.text and "x" * 20 in caplog.text


def test_the_repository_limit_is_logged_once_with_the_count_only(tmp_path: Path, caplog: Any) -> None:
    fake = program(tmp_path, "fake-gh", fixture("github_repos.json"))
    GitHub(settings_for("gh", fake, max_repositories_per_source=1)).repositories("acme-org")
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1 and "limit" in messages[0] and "1" in messages[0]
    assert "web-shop" not in caplog.text and "acme-org" not in caplog.text
