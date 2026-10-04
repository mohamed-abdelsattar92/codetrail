"""The exclusion rules: built-in secret patterns and gitignore-style ignore lines (design section 3.3)."""

import pytest

from codetrail.repo.rules import ExclusionRules, Reason

SECRETS = [
    ".env",
    ".env.local",
    ".env.production",
    ".dev.vars",
    "AuthKey_ABC.p8",
    "cert.p12",
    "server.pem",
    "release.keystore",
    "upload.jks",
    "terraform.tfstate",
    "terraform.tfstate.backup",
    "prod.tfvars",
    "prod.auto.tfvars.json",
    "service.key",
    "cert.pfx",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "putty.ppk",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".terraform/providers/lock",
]


@pytest.mark.parametrize("name", SECRETS)
@pytest.mark.parametrize("folder", ["", "services/api/", "infra/envs/dev/"])
def test_built_in_patterns_exclude_secrets_anywhere(folder: str, name: str) -> None:
    assert ExclusionRules([]).reason(folder + name) is Reason.SECRET_PATTERN


@pytest.mark.parametrize("path", [".env.example", "services/api/.env.example", "terraform.tfvars.example"])
def test_example_files_are_not_secrets(path: str) -> None:
    assert ExclusionRules([]).reason(path) is None


def test_examples_inside_an_excluded_folder_stay_excluded() -> None:
    assert ExclusionRules([]).reason(".terraform/settings.example") is Reason.SECRET_PATTERN


@pytest.mark.parametrize("line", ["!.env", "!**/.env", "!*.pem", "!**/*.pem", "!.terraform/", "!*"])
def test_no_ignore_line_can_bring_back_a_secret(line: str) -> None:
    rules = ExclusionRules([line])
    assert rules.reason(".env") is Reason.SECRET_PATTERN
    assert rules.reason("keys/server.pem") is Reason.SECRET_PATTERN
    assert rules.reason(".terraform/x") is Reason.SECRET_PATTERN


@pytest.mark.parametrize("path", ["README.md", "src/app.py", "docs/environment.md", "envelope.txt", "keys.py"])
def test_ordinary_files_are_visible(path: str) -> None:
    assert ExclusionRules([]).reason(path) is None


@pytest.mark.parametrize(
    ("lines", "path", "excluded"),
    [
        (["docs/"], "docs/a.md", True),
        (["docs/"], "src/docs/b.md", True),
        (["/build"], "build/out.txt", True),
        (["/build"], "src/build/out.txt", False),
        (["**/*.png"], "a/b/c.png", True),
        (["**/*.png"], "a/b/c.jpg", False),
        (["a/**/b"], "a/x/y/b", True),
        (["*.png", "!keep.png"], "keep.png", False),
        (["*.png", "!keep.png"], "other.png", True),
        (["# a comment", "", "   "], "README.md", False),
        (["secret?.txt"], "secret1.txt", True),
        (["data[0-9].csv"], "data7.csv", True),
    ],
)
def test_ignore_lines_follow_gitignore(lines: list[str], path: str, excluded: bool) -> None:
    expected = Reason.IGNORED if excluded else None
    assert ExclusionRules(lines).reason(path) is expected
