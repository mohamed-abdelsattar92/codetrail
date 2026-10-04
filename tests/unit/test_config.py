"""Configuration and Codetrail's folders (design sections 2.3 and 9)."""

from pathlib import Path

import pytest

from codetrail.config import Paths, load_global, load_target, validate_target_name, write_target
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
