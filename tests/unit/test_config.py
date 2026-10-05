"""Configuration and Codetrail's folders (design sections 2.3 and 9)."""

from pathlib import Path

import pytest

from codetrail.config import (
    Paths,
    check_containment,
    load_global,
    load_target,
    validate_target_name,
    write_target,
)
from codetrail.errors import CodetrailError


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
    assert paths.target_file("hamesh") == tmp_path / ".config" / "codetrail" / "targets" / "hamesh.toml"
    assert paths.ignore_file("hamesh") == tmp_path / ".config" / "codetrail" / "targets" / "hamesh.ignore"
    assert paths.target_data("hamesh") == tmp_path / ".local" / "share" / "codetrail" / "hamesh"


@pytest.mark.parametrize("name", ["hamesh", "my-repo", "a", "a" * 63, "repo2"])
def test_accepts_plain_target_names(name: str) -> None:
    assert validate_target_name(name) == name


@pytest.mark.parametrize("name", ["", "../x", "Hamesh", "a b", "a" * 64, "-x", "x/y", ".hidden"])
def test_refuses_other_target_names(name: str) -> None:
    with pytest.raises(CodetrailError):
        validate_target_name(name)


def test_global_defaults_apply_without_a_file(paths: Paths) -> None:
    assert load_global(paths).tools.gitleaks == "gitleaks"


def test_unknown_global_keys_are_refused_by_name(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text('[tools]\ngitleaks = "gitleaks"\ncolour = "red"\n')
    with pytest.raises(CodetrailError, match="colour"):
        load_global(paths)


def test_a_missing_target_names_the_command_that_adds_it(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="codetrail target add"):
        load_target(paths, "hamesh")


def test_written_targets_round_trip(paths: Paths, tmp_path: Path) -> None:
    repository = tmp_path / 'my "odd" repö'
    write_target(paths, "hamesh", repository, "develop")
    target = load_target(paths, "hamesh")
    assert target.repository == repository
    assert target.branch == "develop"


def test_a_target_is_never_overwritten(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "hamesh", tmp_path / "repo", "develop")
    with pytest.raises(CodetrailError, match="already exists"):
        write_target(paths, "hamesh", tmp_path / "other", "main")


def test_unknown_target_keys_are_refused_by_name(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "hamesh", tmp_path / "repo", "develop")
    file = paths.target_file("hamesh")
    file.write_text(file.read_text() + 'extra = "x"\n')
    with pytest.raises(CodetrailError, match="extra"):
        load_target(paths, "hamesh")


def test_the_repository_may_not_contain_codetrails_folders(paths: Paths, tmp_path: Path) -> None:
    with pytest.raises(CodetrailError, match="inside"):
        write_target(paths, "hamesh", tmp_path, "develop")


def test_the_repository_may_not_sit_inside_codetrails_folders(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="inside"):
        write_target(paths, "hamesh", paths.data_dir / "repo", "develop")


def test_home_is_expanded_in_the_repository_path(paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths.target_file("hamesh").parent.mkdir(parents=True)
    paths.target_file("hamesh").write_text('repository = "~/repo"\nbranch = "develop"\n')
    assert load_target(paths, "hamesh").repository == tmp_path / "home" / "repo"


def test_relative_xdg_values_are_ignored(tmp_path: Path) -> None:
    paths = Paths.from_environment({"HOME": str(tmp_path), "XDG_DATA_HOME": ".cache", "XDG_CONFIG_HOME": "rel"})
    assert paths.data_dir == tmp_path / ".local" / "share" / "codetrail"
    assert paths.config_dir == tmp_path / ".config" / "codetrail"


def test_containment_is_checked_again_after_the_target_was_added(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "hamesh", tmp_path / "repo", "develop")
    moved = Paths(config_dir=paths.config_dir, data_dir=tmp_path / "repo" / ".cache", state_dir=paths.state_dir)
    with pytest.raises(CodetrailError, match="inside"):
        check_containment(moved, load_target(paths, "hamesh").repository)


def test_control_characters_survive_the_toml_round_trip(paths: Paths, tmp_path: Path) -> None:
    repository = tmp_path / "odd\x7fname"
    write_target(paths, "hamesh", repository, "develop")
    assert load_target(paths, "hamesh").repository == repository


def test_invalid_branch_names_are_refused(paths: Paths, tmp_path: Path) -> None:
    with pytest.raises(CodetrailError, match="branch"):
        write_target(paths, "hamesh", tmp_path / "repo", "bad..name")
    assert not paths.target_file("hamesh").exists()


def test_server_and_page_defaults(paths: Paths) -> None:
    settings = load_global(paths)
    assert settings.server.port == 8765
    assert settings.server.login_code_ttl_seconds == 60
    assert settings.ui.default_language == "en"
    assert settings.signal.cache_seconds == 60
    assert settings.diagrams.max_nodes == 60


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
    write_target(paths, "hamesh", tmp_path / "repo", "develop")
    target = load_target(paths, "hamesh")
    assert target.generation.max_pages_per_update == 20
    assert target.generation.concurrency == 2
    assert target.generation.max_turns == 30
    assert target.generation.max_budget_usd_per_call == 1.0
    assert target.models.plan == "claude-opus-5-5"
    assert target.models.write == "claude-sonnet-5-5"
    assert target.models.digest == "claude-sonnet-5-5"


def test_generation_limits_must_be_positive(paths: Paths, tmp_path: Path) -> None:
    write_target(paths, "hamesh", tmp_path / "repo", "develop")
    file = paths.target_file("hamesh")
    file.write_text(file.read_text() + "[generation]\nconcurrency = 0\n")
    with pytest.raises(CodetrailError, match="concurrency"):
        load_target(paths, "hamesh")
