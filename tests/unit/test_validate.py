"""Validation before a page is saved: documented quotes, fact links, diagrams and checks (design section 6.5)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Source
from codetrail.facts.store import FactStore
from codetrail.generate.validate import ValidationContext, parse_rationale, validate_page
from codetrail.repo.mirror import refresh_mirror
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest
from tests.fixtures.repos import add_commit, fake_github_token, git, make_repository

COMMITS: dict[str, str] = {}
ADR_TEXT = (
    "# 0007. Use REST\n\n- Status: accepted\n\n## Decision\nUse REST with the OpenAPI document\nas the contract.\n"
)


@pytest.fixture
def context(tmp_path: Path) -> ValidationContext:
    checkout = make_repository(tmp_path / "target", [{"docs/adr/0007-rest.md": ADR_TEXT, "app/main.py": "x = 1\n"}])
    add_commit(checkout, {"app/main.py": "x = 2\n"}, "fix(app): change x\n\nWhy: the old value broke the build")
    add_commit(checkout, {"app/other.py": "y = 1\n"}, f"chore: oops\n\nWhy: rotated {fake_github_token()}")
    mirror = tmp_path / "mirror.git"
    head = refresh_mirror(mirror, checkout, "develop")
    source = tmp_path / "source"
    (source / "docs" / "adr").mkdir(parents=True)
    (source / "docs" / "adr" / "0007-rest.md").write_text(ADR_TEXT)
    (source / "app").mkdir()
    (source / "app" / "main.py").write_text("x = 2\n")
    store = FactStore(connect(tmp_path / "codetrail.db"))
    store.record(head, [
        Entity("decision:ADR-0007", EntityKind.DECISION, {}, (Source("docs/adr/0007-rest.md"),)),
        Entity("project:.", EntityKind.PROJECT, {}, (Source("pyproject.toml"),)),
    ], [])  # fmt: skip
    manifest = SourceManifest(head, {"docs/adr/0007-rest.md": "a" * 40, "app/main.py": "b" * 40})
    COMMITS["fix"] = git(checkout, "rev-parse", "HEAD~1")
    COMMITS["secret"] = git(checkout, "rev-parse", "HEAD")
    return ValidationContext(source, manifest, store, mirror, SecretScanner("gitleaks"))


def documented(citation: str, quote: str) -> str:
    return f'> [!documented] {citation}\n> "{quote}"\n'


GOOD_CHECKS = [
    {"id": "why-rest", "question": "Why REST?", "rubric": [{"point": "A contract", "grounds": ["decision:ADR-0007"]}]}
]


def test_a_correct_page_passes(context: ValidationContext) -> None:
    body = (
        "Overview.\n\n"
        + documented("docs/adr/0007-rest.md#L6-L7", "Use REST with the OpenAPI document as the contract.")
        + "\n> [!inferred]\n> It keeps clients generated.\n\n"
        + "See [[decision:ADR-0007]].\n\n{{diagram imports scope=app}}\n{{diagram dependencies project=project:.}}\n"
    )
    assert validate_page(body, GOOD_CHECKS, context) == []


def test_a_quote_from_a_commit_passes(context: ValidationContext) -> None:
    body = documented(f"commit:{COMMITS['fix'][:10]}", "the old value broke the build")
    assert validate_page(body, GOOD_CHECKS, context) == []


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (documented("docs/adr/0007-rest.md#L6-L7", "Use GraphQL everywhere"), "isn't in"),
        (documented("docs/adr/0007-rest.md#L1-L2", "Use REST with the OpenAPI document"), "isn't in"),
        (documented("docs/adr/0007-rest.md#L6-L90", "Use REST"), "lines"),
        (documented(".env#L1-L1", "SECRET"), "isn't a visible file"),
        (documented("docs/adr/0007-rest.md", "Use REST"), "citation"),
        (documented("commit:deadbeefdeadbeef", "anything"), "commit"),
        (documented("docs/adr/0007-rest.md#L6-L7", ""), "quote"),
        ("See [[module:ghost.py]].", "ghost.py"),
        ("{{diagram imports scope=nowhere}}", "nowhere"),
        ("{{diagram dependencies project=project:missing}}", "project:missing"),
        ("{{diagram sequence scope=app}}", "diagram"),
        ("", "empty"),
    ],
)
def test_bad_pages_fail_with_a_clear_problem(context: ValidationContext, body: str, problem: str) -> None:
    problems = validate_page(body, GOOD_CHECKS, context)
    assert problems and any(problem in found for found in problems), problems


def test_a_quote_from_a_flagged_commit_message_fails(context: ValidationContext) -> None:
    problems = validate_page(documented(f"commit:{COMMITS['secret']}", "rotated"), GOOD_CHECKS, context)
    assert any("withheld" in problem for problem in problems)


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [{"id": "x", "question": "Q?", "rubric": []}],
        [{"id": "x", "question": "Q?", "rubric": [{"point": "p", "grounds": ["module:ghost.py"]}]}],
        [{"id": "x", "question": "", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}],
    ],
)
def test_checks_must_be_grounded(context: ValidationContext, checks: list[dict[str, object]]) -> None:
    assert validate_page("Overview.", checks, context)


def test_checks_may_be_grounded_in_paths(context: ValidationContext) -> None:
    checks = [{"id": "x", "question": "Q?", "rubric": [{"point": "p", "grounds": ["app/main.py", "docs/adr"]}]}]
    assert validate_page("Overview.", checks, context) == []


def test_rationale_blocks_are_parsed() -> None:
    blocks = parse_rationale('> [!documented] a.md#L1-L2\n> "one\n> two"\n\n> [!inferred]\n> mine\n')
    assert [(block.kind, block.citation, block.text) for block in blocks] == [
        ("documented", "a.md#L1-L2", '"one\ntwo"'),
        ("inferred", None, "mine"),
    ]
