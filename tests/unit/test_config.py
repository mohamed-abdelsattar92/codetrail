"""Configuration and Codetrail's folders (design sections 2.3 and 9)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from codetrail.config import (
    TEAM_NAME,
    AgentSettings,
    EstimateSettings,
    GlobalConfig,
    Paths,
    RemoteSettings,
    TargetConfig,
    TeamSettings,
    TeamSource,
    TokenGuess,
    check_containment,
    load_global,
    load_target,
    load_team,
    model_choice,
    validate_target_name,
    validate_team_name,
    write_remote_target,
    write_target,
)
from codetrail.errors import CodetrailError
from codetrail.hosting.names import check_clone_url
from codetrail.team.names import team_target_name


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")


def test_paths_follow_the_xdg_variables(tmp_path: Path) -> None:
    environ = {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "c"),
        "XDG_DATA_HOME": str(tmp_path / "d"),
        "XDG_STATE_HOME": str(tmp_path / "s"),
    }
    paths = Paths.from_environment(environ)
    assert paths.config_dir == tmp_path / "c" / "codetrail"
    assert paths.data_dir == tmp_path / "d" / "codetrail"
    assert paths.state_dir == tmp_path / "s" / "codetrail"


def test_paths_default_under_the_home_folder(tmp_path: Path) -> None:
    paths = Paths.from_environment({"HOME": str(tmp_path)})
    assert paths.config_dir == tmp_path / ".config" / "codetrail"
    assert paths.data_dir == tmp_path / ".local" / "share" / "codetrail"
    assert paths.state_dir == tmp_path / ".local" / "state" / "codetrail"
    assert paths.target_file("shop") == tmp_path / ".config" / "codetrail" / "targets" / "shop.toml"
    assert paths.ignore_file("shop") == tmp_path / ".config" / "codetrail" / "targets" / "shop.ignore"
    assert paths.target_data("shop") == tmp_path / ".local" / "share" / "codetrail" / "shop"


@pytest.mark.parametrize("name", ["shop", "my-repo", "a", "a" * 63, "repo2"])
def test_accepts_plain_target_names(name: str) -> None:
    assert validate_target_name(name) == name


@pytest.mark.parametrize("name", ["", "../x", "Shop", "a b", "a" * 64, "-x", "x/y", ".hidden"])
def test_refuses_other_target_names(name: str) -> None:
    with pytest.raises(CodetrailError):
        validate_target_name(name)


def test_global_defaults_apply_without_a_file(paths: Paths) -> None:
    assert load_global(paths).tools.gitleaks == "gitleaks"


def test_the_gitleaks_timeout_defaults_to_ten_minutes_and_can_be_set(paths: Paths) -> None:
    assert load_global(paths).tools.gitleaks_timeout_seconds == 600
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text("[tools]\ngitleaks_timeout_seconds = 1800\n")
    assert load_global(paths).tools.gitleaks_timeout_seconds == 1800


@pytest.mark.parametrize("timeout", ["0", "-5", "86_401", "inf"])
def test_a_gitleaks_timeout_outside_a_day_is_refused(paths: Paths, timeout: str) -> None:
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(f"[tools]\ngitleaks_timeout_seconds = {timeout}\n")
    with pytest.raises(CodetrailError, match="gitleaks_timeout_seconds"):
        load_global(paths)


def test_unknown_global_keys_are_refused_by_name(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text('[tools]\ngitleaks = "gitleaks"\ncolour = "red"\n')
    with pytest.raises(CodetrailError, match="colour"):
        load_global(paths)


def test_a_missing_target_names_the_command_that_adds_it(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="codetrail target add"):
        load_target(paths, "shop")


def test_written_targets_round_trip(paths: Paths, tmp_path: Path) -> None:
    repository = tmp_path / 'my "odd" repö'
    write_target(paths, "shop", repository, "develop")
    target = load_target(paths, "shop")
    assert target.repository == repository
    assert target.branch == "develop"


def test_a_target_is_never_overwritten(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    with pytest.raises(CodetrailError, match="already exists"):
        write_target(paths, "shop", tmp_path / "other", "main")


def test_unknown_target_keys_are_refused_by_name(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    file = paths.target_file("shop")
    file.write_text(file.read_text() + 'extra = "x"\n')
    with pytest.raises(CodetrailError, match="extra"):
        load_target(paths, "shop")


def test_the_repository_may_not_contain_codetrails_folders(paths: Paths, tmp_path: Path) -> None:
    with pytest.raises(CodetrailError, match="inside"):
        write_target(paths, "shop", tmp_path, "develop")


def test_the_repository_may_not_sit_inside_codetrails_folders(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="inside"):
        write_target(paths, "shop", paths.data_dir / "repo", "develop")


def test_home_is_expanded_in_the_repository_path(paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths.target_file("shop").parent.mkdir(parents=True)
    paths.target_file("shop").write_text('repository = "~/repo"\nbranch = "develop"\n')
    assert load_target(paths, "shop").repository == tmp_path / "home" / "repo"


def test_relative_xdg_values_are_ignored(tmp_path: Path) -> None:
    paths = Paths.from_environment({"HOME": str(tmp_path), "XDG_DATA_HOME": ".cache", "XDG_CONFIG_HOME": "rel"})
    assert paths.data_dir == tmp_path / ".local" / "share" / "codetrail"
    assert paths.config_dir == tmp_path / ".config" / "codetrail"


def test_containment_is_checked_again_after_the_target_was_added(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    moved = Paths(config_dir=paths.config_dir, data_dir=tmp_path / "repo" / ".cache", state_dir=paths.state_dir)
    with pytest.raises(CodetrailError, match="inside"):
        check_containment(moved, tmp_path / "repo")


def test_control_characters_survive_the_toml_round_trip(paths: Paths, tmp_path: Path) -> None:
    repository = tmp_path / "odd\x7fname"
    write_target(paths, "shop", repository, "develop")
    assert load_target(paths, "shop").repository == repository


def test_invalid_branch_names_are_refused(paths: Paths, tmp_path: Path) -> None:
    with pytest.raises(CodetrailError, match="branch"):
        write_target(paths, "shop", tmp_path / "repo", "bad..name")
    assert not paths.target_file("shop").exists()


def test_server_and_page_defaults(paths: Paths) -> None:
    settings = load_global(paths)
    assert settings.server.port == 8765
    assert settings.server.login_code_ttl_seconds == 60
    assert settings.ui.default_language == "en"
    assert settings.signal.cache_seconds == 60
    assert settings.diagrams.max_nodes == 25


@pytest.mark.parametrize(
    "text",
    [
        "[server]\nport = 80\n",
        "[server]\nport = 70000\n",
        "[diagrams]\nmax_nodes = 0\n",
        "[server]\nhost = '0.0.0.0'\n",
    ],
)
def test_unsafe_or_impossible_settings_are_refused(paths: Paths, text: str) -> None:
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(text)
    with pytest.raises(CodetrailError):
        load_global(paths)


def test_generation_defaults(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    target = load_target(paths, "shop")
    assert target.generation.max_pages_per_update == 20
    assert target.generation.concurrency == 2
    assert target.generation.max_turns == 30
    assert target.generation.max_budget_usd_per_call == 1.0
    assert target.models.plan == "claude-opus-5-5"
    assert target.models.write == "claude-sonnet-5-5"
    assert target.models.digest == "claude-sonnet-5-5"


def test_generation_limits_must_be_positive(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    file = paths.target_file("shop")
    file.write_text(file.read_text() + "[generation]\nconcurrency = 0\n")
    with pytest.raises(CodetrailError, match="concurrency"):
        load_target(paths, "shop")


def test_provider_defaults_use_the_subscription(paths: Paths) -> None:
    settings = load_global(paths)
    assert settings.assistant.retry_attempts == 2
    assert (settings.providers.claude_code.command, settings.providers.claude_code.auth) == ("claude", "subscription")
    assert (settings.providers.codex.command, settings.providers.codex.auth) == ("codex", "subscription")
    assert settings.providers.local.base_url == "http://127.0.0.1:11434/v1"
    assert settings.prices["claude-sonnet-5-5"].input == 2.0


def test_provider_settings_are_read_and_checked(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    config = paths.config_dir / "config.toml"
    config.write_text('[providers.claude_code]\nauth = "api_key"\n[prices."my-model"]\ninput = 1.5\noutput = 6.0\n')
    settings = load_global(paths)
    assert settings.providers.claude_code.auth == "api_key"
    assert settings.prices["my-model"].output == 6.0
    assert "claude-opus-5-5" in settings.prices  # the defaults stay unless replaced
    config.write_text('[providers.local]\nbase_url = "http://example.com/v1"\n')
    with pytest.raises(CodetrailError, match="loopback"):
        load_global(paths)
    config.write_text('[providers.codex]\nauth = "token"\n')
    with pytest.raises(CodetrailError, match="auth"):
        load_global(paths)


def test_models_name_their_provider(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "main")
    file = paths.target_file("shop")
    file.write_text(file.read_text() + '[models]\nanswer = "local:qwen3:14b"\nplan = "claude_code:claude-opus-5-5"\n')
    target = load_target(paths, "shop")
    assert model_choice(target.models.answer) == ("local", "qwen3:14b")
    assert model_choice(target.models.plan) == ("claude_code", "claude-opus-5-5")
    assert model_choice(target.models.write) == ("claude_code", "claude-sonnet-5-5")  # no prefix: Claude Code


def test_an_unknown_provider_is_refused(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "main")
    file = paths.target_file("shop")
    file.write_text(file.read_text() + '[models]\nanswer = "gemini:pro"\n')
    with pytest.raises(CodetrailError, match="gemini"):
        load_target(paths, "shop")


def test_codex_needs_the_target_to_opt_in(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "main")
    file = paths.target_file("shop")
    base = file.read_text()
    file.write_text(base + '[models]\nwrite = "codex:gpt-5.5-codex"\n')
    with pytest.raises(CodetrailError, match="allow_codex"):
        load_target(paths, "shop")
    file.write_text(base + '[assistant]\nallow_codex = true\n[models]\nwrite = "codex:gpt-5.5-codex"\n')
    assert load_target(paths, "shop").assistant.allow_codex


def test_search_and_session_answer_defaults(paths: Paths) -> None:
    settings = load_global(paths)
    assert settings.search.max_query_chars == 200
    assert settings.search.max_results == 20
    assert settings.bridge.max_session_answers == 20


def test_search_settings_are_read_from_the_file(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(
        "[search]\nmax_query_chars = 80\nmax_results = 5\n\n[bridge]\nmax_session_answers = 3\n"
    )
    settings = load_global(paths)
    assert (settings.search.max_query_chars, settings.search.max_results) == (80, 5)
    assert settings.bridge.max_session_answers == 3


@pytest.mark.parametrize(
    "toml", ["[search]\nmax_query_chars = 0\n", "[search]\nmax_results = -1\n", "[bridge]\nmax_session_answers = 0\n"]
)
def test_search_limits_must_be_positive(paths: Paths, toml: str) -> None:
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(toml)
    with pytest.raises(CodetrailError):
        load_global(paths)


def test_metrics_settings_have_their_defaults(paths: Paths, tmp_path: Path) -> None:
    settings = load_global(paths)
    assert (settings.metrics.commit_window, settings.metrics.proposed_adr_days) == (200, 30)
    assert (settings.metrics.trend_updates, settings.metrics.max_listed) == (12, 50)
    write_target(paths, "shop", tmp_path / "repo", "develop")
    metrics = load_target(paths, "shop").metrics
    assert "docs/**" in metrics.document_globs
    assert metrics.why.search("Why: because")
    assert metrics.why.search("What: this\n\n## Why\nbecause")
    assert not metrics.why.search("Nobody asked why.")
    assert "\\s" not in metrics.commit_why_pattern  # whitespace that crosses lines makes matching quadratic
    assert load_global(paths).metrics.max_message_chars == 20_000


def test_an_invalid_why_pattern_is_refused_by_name(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    file = paths.target_file("shop")
    file.write_text(file.read_text() + "[metrics]\ncommit_why_pattern = '(unclosed'\n")
    with pytest.raises(CodetrailError, match="commit_why_pattern"):
        load_target(paths, "shop")


def test_repository_statistics_settings_have_their_defaults(paths: Paths, tmp_path: Path) -> None:
    settings = load_global(paths)
    assert (settings.metrics.activity_months, settings.metrics.history_limit) == (24, 1_000_000)
    assert settings.metrics.largest_files == 10
    assert (settings.metrics.churn_window, settings.metrics.quiet_days) == (500, 365)
    assert (settings.metrics.folder_depth, settings.metrics.heatmap_tiles) == (2, 60)
    assert settings.metrics.languages[".py"] == "Python"
    assert settings.metrics.languages["dockerfile"] == "Dockerfile"
    write_target(paths, "shop", tmp_path / "repo", "develop")
    metrics = load_target(paths, "shop").metrics
    assert "tests/**" in metrics.test_globs
    found = metrics.types.match("feat(web): add the page")
    assert found and (found["type"], found["scope"]) == ("feat", "web")
    assert metrics.types.match("fix!: drop it")
    assert not metrics.types.match("Add the page")
    assert "\\s" not in metrics.commit_type_pattern


@pytest.mark.parametrize("pattern", ["^(feat|fix):", "(unclosed"])
def test_a_type_pattern_without_a_type_group_is_refused_by_name(paths: Paths, tmp_path: Path, pattern: str) -> None:
    write_target(paths, "shop", tmp_path / "repo", "develop")
    file = paths.target_file("shop")
    file.write_text(file.read_text() + f"[metrics]\ncommit_type_pattern = '{pattern}'\n")
    with pytest.raises(CodetrailError, match="commit_type_pattern"):
        load_target(paths, "shop")


def test_language_names_are_lower_cased(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "config.toml").write_text('[metrics.languages]\n".PY" = "Python"\n')
    assert load_global(paths).metrics.languages == {".py": "Python"}


def test_quiet_days_are_bounded(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "config.toml").write_text("[metrics]\nquiet_days = 10000000\n")
    with pytest.raises(CodetrailError, match="quiet_days"):
        load_global(paths)


@pytest.mark.parametrize("value", [0, 51])
def test_refuses_a_pin_limit_out_of_range(value: int) -> None:
    with pytest.raises(ValidationError):
        GlobalConfig.model_validate({"ui": {"max_pins": value}})


def test_agent_settings_have_their_defaults_and_ranges() -> None:
    settings = GlobalConfig().agents
    assert (settings.max_map_words, settings.max_brief_words, settings.key_files_per_part) == (2500, 300, 5)
    assert (settings.recent_digests, settings.max_briefs_per_call, settings.max_response_words) == (5, 3, 4000)
    for field, value in (("max_map_words", 499), ("max_brief_words", 2001), ("max_briefs_per_call", 0)):
        with pytest.raises(ValidationError):
            AgentSettings(**{field: value})


def test_the_estimates_leave_room_for_a_brief() -> None:
    estimates = GlobalConfig().estimates
    assert (estimates.write.output, estimates.revise.output) == (6_500, 2_500)


REMOTE = {
    "service": "github",
    "source": "acme-org",
    "id": "R_kgDOExample",
    "name": "web-shop",
    "url": "https://github.com/acme-org/web-shop.git",
}


def test_a_target_has_a_repository_or_a_remote_not_both(tmp_path: Path) -> None:
    assert TargetConfig(repository=tmp_path, branch="main").remote is None
    assert TargetConfig(remote=REMOTE, branch="main").repository is None  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="exactly one"):
        TargetConfig(branch="main")
    with pytest.raises(ValidationError, match="exactly one"):
        TargetConfig(repository=tmp_path, remote=REMOTE, branch="main")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("service", "url"),
    [
        ("github", "https://example.com/x.git"),
        ("github", "https://gitlab.com/acme-org/web-shop.git"),
        ("github", "http://github.com/acme-org/web-shop.git"),
        ("github", "https://user@github.com/acme-org/web-shop.git"),
        ("github", "https://github.com:8443/acme-org/web-shop.git"),
        ("github", "https://github.com.evil.example/acme-org/web-shop.git"),
        ("github", "https://github.com/acme-org/web-shop.git#fragment"),
        ("github", "git@github.com:-oProxyCommand=x"),
        ("azure_devops", "https://a:b@dev.azure.com/a/p/_git/r"),
        ("github", "git://github.com/acme-org/web-shop.git"),
        ("github", "file:///tmp/repo"),
        ("github", "git@example.com:acme-org/web-shop.git"),
        ("github", "ssh://git@github.com.example.com/acme-org/web-shop.git"),
        ("github", "ssh://other@github.com/acme-org/web-shop.git"),
        ("azure_devops", "https://other@dev.azure.com/contoso/Platform/_git/web-shop"),
        ("gitlab", "git@ssh.dev.azure.com:v3/contoso/Platform/web-shop"),
    ],
)
def test_a_remote_url_on_another_host_is_refused(service: str, url: str) -> None:
    with pytest.raises(CodetrailError, match="clone URL"):
        check_clone_url(service, url)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RemoteSettings.model_validate({**REMOTE, "service": service, "url": url})


@pytest.mark.parametrize(
    ("service", "url", "accepted"),
    [
        ("github", "https://github.com/acme-org/web-shop.git", None),
        ("github", "git@github.com:acme-org/web-shop.git", None),
        ("github", "ssh://git@github.com/acme-org/web-shop.git", None),
        ("gitlab", "https://gitlab.com/example-group/web-shop.git", None),
        ("gitlab", "git@gitlab.com:example-group/web-shop.git", None),
        ("azure_devops", "https://dev.azure.com/contoso/Platform/_git/web-shop", None),
        ("azure_devops", "git@ssh.dev.azure.com:v3/contoso/Platform/web-shop", None),
        # The organization as the user name is how Azure DevOps writes it; it is dropped, so git never sees a user.
        (
            "azure_devops",
            "https://contoso@dev.azure.com/contoso/Platform/_git/Payments%20API%20%232",
            "https://dev.azure.com/contoso/Platform/_git/Payments%20API%20%232",
        ),
    ],
)
def test_clone_urls_on_the_services_hosts_are_accepted(service: str, url: str, accepted: str | None) -> None:
    assert check_clone_url(service, url) == (accepted or url)  # type: ignore[arg-type]


def test_location_is_the_path_or_the_url(tmp_path: Path) -> None:
    assert TargetConfig(repository=tmp_path, branch="main").location == str(tmp_path)
    assert TargetConfig(remote=REMOTE, branch="main").location == REMOTE["url"]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "[hosting]\ntimeout_seconds = 4",
        "[hosting]\ntimeout_seconds = 601",
        "[hosting]\nmax_output_bytes = 99_999",
        "[hosting]\nmax_output_bytes = 100_000_001",
        "[hosting]\nmax_repositories_per_source = 0",
        "[hosting]\nmax_repositories_per_source = 10_001",
        "[hosting]\nconcurrency = 0",
        "[hosting]\nconcurrency = 17",
        "[team]\nactive_days = 0",
        "[team]\nactive_days = 3651",
        "[team]\nhighlights_per_digest = -1",
        "[team]\nhighlights_per_digest = 11",
        "[team]\nmax_overview_words = 49",
        "[team]\nmax_overview_words = 1001",
    ],
)
def test_hosting_and_team_settings_refuse_values_out_of_range(paths: Paths, text: str) -> None:
    (paths.config_dir).mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(text, encoding="utf-8")
    with pytest.raises(CodetrailError):
        load_global(paths)


def test_hosting_and_team_defaults() -> None:
    config = GlobalConfig()
    assert (config.hosting.gh, config.hosting.az, config.hosting.glab) == ("gh", "az", "glab")
    assert config.hosting.timeout_seconds == 60
    assert config.hosting.max_output_bytes == 10_000_000
    assert config.hosting.max_repositories_per_source == 1000
    assert config.hosting.concurrency == 4
    assert config.team.active_days == 90


def test_team_names() -> None:
    assert validate_team_name("acme") == "acme"
    assert TEAM_NAME.fullmatch("a" * 30)
    for name in ("Acme", "-acme", "a" * 31, "", "ac me"):
        with pytest.raises(CodetrailError, match="team name"):
            validate_team_name(name)


def test_teams_is_not_a_target_name() -> None:
    with pytest.raises(CodetrailError, match="reserved"):
        validate_target_name("teams")


def test_a_team_file_is_read_and_checked(paths: Paths) -> None:
    assert paths.team_file("acme") == paths.config_dir / "teams" / "acme.toml"
    assert paths.team_data("acme") == paths.data_dir / "teams" / "acme"
    paths.team_file("acme").parent.mkdir(parents=True)
    paths.team_file("acme").write_text(
        '[[sources]]\nservice = "github"\nsource = "acme-org"\nleft_out = ["old-site"]\n', encoding="utf-8"
    )
    source = load_team(paths, "acme").sources[0]
    assert (source.service, source.source, source.follow, source.clone_over) == ("github", "acme-org", "all", "https")
    assert source.left_out == ["old-site"]
    paths.team_file("acme").write_text('[[sources]]\nservice = "github"\nsource = "a"\nclone_over = "git"\n')
    with pytest.raises(CodetrailError):
        load_team(paths, "acme")
    with pytest.raises(CodetrailError, match="No team named"):
        load_team(paths, "missing")


def test_write_remote_target_round_trips(paths: Paths) -> None:
    remote = RemoteSettings.model_validate(REMOTE)
    file = write_remote_target(paths, "acme-web-shop", remote, "main", "acme")
    assert file == paths.target_file("acme-web-shop")
    loaded = load_target(paths, "acme-web-shop")
    assert loaded.remote is not None
    assert loaded.remote.id == "R_kgDOExample"
    assert loaded.branch == "main"
    assert loaded.team == "acme"
    with pytest.raises(CodetrailError, match="already exists"):
        write_remote_target(paths, "acme-web-shop", remote, "main", "acme")


def test_target_names_from_awkward_repository_names() -> None:
    assert team_target_name("acme", "Payments API #2", set()) == "acme-payments-api-2"
    long_name = team_target_name("acme", "x" * 70, set())
    assert len(long_name) <= 63
    assert validate_target_name(long_name) == long_name
    assert team_target_name("acme", "web-shop", {"acme-web-shop"}, "R_kgDOExample") == "acme-web-shop-rkgdoe"
    clash_of_long = team_target_name("acme", "x" * 70, {long_name}, "R_kgDOExample")
    assert clash_of_long.endswith("-rkgdoe")
    assert len(clash_of_long) <= 63
    assert clash_of_long != long_name


def test_the_model_stores_the_normalized_azure_url() -> None:
    remote = RemoteSettings.model_validate(
        {
            **REMOTE,
            "service": "azure_devops",
            "source": "contoso/Platform",
            "url": "https://contoso@dev.azure.com/contoso/Platform/_git/web-shop",
        }
    )
    assert remote.url == "https://dev.azure.com/contoso/Platform/_git/web-shop"


def test_a_team_source_and_a_target_team_are_validated() -> None:
    assert TeamSource(service="github", source="acme-org").source == "acme-org"
    with pytest.raises(ValidationError):
        TeamSource(service="github", source="../x")
    with pytest.raises(ValidationError):
        TeamSource(service="azure_devops", source="contoso")
    assert TargetConfig(remote=REMOTE, branch="main", team="acme").team == "acme"  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TargetConfig(remote=REMOTE, branch="main", team="Not A Team")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "<script>alert(1)</script>"},
        {"name": ".."},
        {"source": "-acme"},
        {"source": "acme org"},
        {"service": "azure_devops", "source": "contoso", "url": "https://dev.azure.com/contoso/Platform/_git/web-shop"},
    ],
)
def test_a_remote_s_names_are_validated_before_they_are_stored(changes: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        RemoteSettings.model_validate({**REMOTE, **changes})


def test_a_remote_with_an_awkward_azure_name_loads() -> None:
    remote = RemoteSettings.model_validate(
        {
            **REMOTE,
            "service": "azure_devops",
            "source": "contoso/Platform",
            "name": "Payments API",
            "url": "https://dev.azure.com/contoso/Platform/_git/Payments%20API",
        }
    )
    assert (remote.source, remote.name) == ("contoso/Platform", "Payments API")


def test_a_refused_remote_url_is_not_echoed_in_the_settings_error() -> None:
    url = "https://reader:secret@github.com/acme-org/web-shop.git"
    with pytest.raises(ValidationError) as raised:
        RemoteSettings.model_validate({**REMOTE, "url": url})
    messages = " ".join(issue["msg"] for issue in raised.value.errors())  # what the settings' error names
    assert "clone URL" in messages and "secret" not in messages


def test_team_settings_defaults() -> None:
    settings = TeamSettings()
    assert (settings.highlights_per_digest, settings.max_overview_words) == (3, 250)


def test_estimate_overview_default() -> None:
    estimates = EstimateSettings()
    assert estimates.overview == TokenGuess(input=15_000, output=1_500)
    assert estimates.digest.output == 3_500


def test_team_config_models_and_catch_up(paths: Paths) -> None:
    paths.team_file("acme").parent.mkdir(parents=True)
    paths.team_file("acme").write_text(
        '[models]\noverview = "local:qwen3"\n\n[catch_up]\nmax_budget_usd = 5\nconcurrency = 3\n',
        encoding="utf-8",
    )
    team = load_team(paths, "acme")
    assert team.models.overview == "local:qwen3"
    assert (team.catch_up.max_budget_usd, team.catch_up.concurrency) == (5.0, 3)
    paths.team_file("acme").write_text("", encoding="utf-8")
    team = load_team(paths, "acme")
    assert (team.models.overview, team.catch_up.max_budget_usd, team.catch_up.concurrency) == (
        "claude-sonnet-5-5",
        20.0,
        2,
    )


def test_team_overview_refuses_codex(paths: Paths) -> None:
    paths.team_file("acme").parent.mkdir(parents=True)
    paths.team_file("acme").write_text('[models]\noverview = "codex:gpt-5"\n', encoding="utf-8")
    with pytest.raises(CodetrailError, match="Codex"):
        load_team(paths, "acme")
    paths.team_file("acme").write_text('[models]\noverview = "gpt:x"\n', encoding="utf-8")
    with pytest.raises(CodetrailError, match="isn't a provider"):
        load_team(paths, "acme")


@pytest.mark.parametrize("text", ["max_budget_usd = 0", "concurrency = 0", "concurrency = 9"])
def test_catch_up_ranges(paths: Paths, text: str) -> None:
    paths.team_file("acme").parent.mkdir(parents=True)
    paths.team_file("acme").write_text(f"[catch_up]\n{text}\n", encoding="utf-8")
    with pytest.raises(CodetrailError):
        load_team(paths, "acme")
