# Phase 2: Facts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `codetrail update <target>` refreshes the sources, extracts facts with the `python` and `adr` extractors, stores them in SQLite with validity ranges, and prints what was added, changed and removed since the last update.

**Architecture:** `database` opens the target's SQLite file and applies ordered SQL migrations tracked by `PRAGMA user_version`. `facts` defines the fact types and kinds and the store (ADR 0003). `extract` defines the extractor interface, runs the enabled extractors over `source/`, merges entities that several files declare, and resolves references. The `update` command ties refresh, extraction and recording together under the target's lock.

**Tech Stack:** Python 3.14, sqlite3, tree-sitter with tree-sitter-python, tomllib.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 4 and 5.

## Global Constraints
- Extractors read only `source/` (never the mirror or the target) and only the files the manifest lists.
- Entity ids are `<kind>:<natural key>` without line numbers. Module ids are path-based (`module:services/api/app/db.py`); the dotted name is an attribute.
- A fact's hash covers kind, key and attributes, not sources.
- `last_seen` is the last snapshot a version was valid in; `NULL` while current. Storage grows with change only.
- A file that fails to parse is a warning naming the path; the update continues.
- SQL is parameterised; identifiers come from code.
- Kinds in Phase 2: entities `project`, `module`, `package`, `decision`; relations `contains`, `imports`, `depends_on`, `supersedes`.

## Review Focus
1. An update run twice on the same commit must report no changes and add no rows — Task 2.
2. A module renamed in a commit appears as one removed and one added fact, and its relations follow — Task 2/5.
3. A syntactically broken `.py` file produces a warning, not a failed update — Task 4.
4. Relative imports (`from . import x`, `from ..db import y`) in a `src/` layout resolve to the right modules — Task 4.
5. An ADR whose status says "superseded by 0012" produces the `supersedes` edge from 0012 — Task 5.

---

### Task 1: The database and migrations
**Files:** `src/codetrail/database/__init__.py`, `src/codetrail/database/migrations/0001_facts.sql`, `tests/unit/test_database.py`.
**Produces:** `connect(path: Path) -> sqlite3.Connection` (foreign keys on, WAL, `sqlite3.Row`, migrations applied in order, each in a transaction, `user_version` = last applied number). **Tests:** a new file reaches the latest version; reopening applies nothing; a database newer than the code is refused with a `CodetrailError`.

### Task 2: Fact types and the store
**Files:** `src/codetrail/facts/__init__.py` (types and kinds), `src/codetrail/facts/store.py`, `tests/unit/test_fact_store.py`.
**Produces:**
```python
class EntityKind(StrEnum): PROJECT; MODULE; PACKAGE; DECISION
class RelationKind(StrEnum): CONTAINS; IMPORTS; DEPENDS_ON; SUPERSEDES
@dataclass(frozen=True) class Source: path: str; start_line: int | None = None; end_line: int | None = None
@dataclass(frozen=True) class Entity: id: str; kind: EntityKind; attributes: Mapping[str, Any]; sources: tuple[Source, ...]
    hash -> str (property)
@dataclass(frozen=True) class Relation: source_id: str; kind: RelationKind; target_id: str; attributes; sources
    key -> tuple[str, str, str]; hash -> str
@dataclass(frozen=True) class Snapshot: id: int; commit: str; taken_at: str
@dataclass(frozen=True) class FactDiff: added/changed/removed entities (ids) and relations (keys); is_empty
class FactStore:
    def __init__(self, connection: sqlite3.Connection) -> None
    def record(self, commit: str, entities: Iterable[Entity], relations: Iterable[Relation]) -> tuple[Snapshot, FactDiff]
    def latest_snapshot(self) -> Snapshot | None
    def previous_snapshot(self, snapshot: Snapshot) -> Snapshot | None
    def diff(self, snapshot: Snapshot) -> FactDiff
    def entities(self, kind: EntityKind | None = None) -> list[Entity]       # current only
    def relations(self, kind: RelationKind | None = None) -> list[Relation]  # current only
    def entity(self, entity_id: str) -> Entity | None
```
**Tests:** first record adds everything; recording the same facts again gives an empty diff and no new rows; a changed attribute closes the old row and opens a new one (diff "changed"); a missing fact is closed at the previous snapshot (diff "removed"); a moved source line refreshes `sources` without a new row; current queries ignore closed rows; a relation's identity is (source, kind, target).

### Task 3: The extractor interface and runner
**Files:** `src/codetrail/extract/__init__.py`, `tests/unit/test_extract_runner.py`.
**Produces:**
```python
@dataclass(frozen=True) class Reference: source_id: str; kind: RelationKind; target: str; sources: tuple[Source, ...]; attributes: Mapping[str, Any] = {}
@dataclass(frozen=True) class FileFacts: path: str; entities: tuple[Entity, ...] = (); references: tuple[Reference, ...] = ()
class Extractor(Protocol):
    name: str; version: int
    def handles(self, path: str) -> bool
    def prepare(self, paths: Sequence[str]) -> None          # the handled paths, once, before extraction
    def extract(self, path: str, content: bytes) -> FileFacts
    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution
@dataclass(frozen=True) class Resolution: relations: list[Relation]; unresolved: int = 0
@dataclass(frozen=True) class Extraction: entities: list[Entity]; relations: list[Relation]; warnings: list[str]; unresolved: dict[str, int]
def run_extractors(source: Path, paths: Iterable[str], extractors: Sequence[Extractor]) -> Extraction
```
Entities declared by several files are merged (sources united); conflicting attributes keep the first and add a warning. A relation whose target isn't a known entity is dropped and counted. An exception in `extract` becomes a warning `<extractor>: <path>: could not be read` (no content).
**Tests:** with two fake extractors: merging, conflicting attributes, an exception becoming a warning, dangling relations dropped and counted, only handled paths offered.

### Task 4: The python extractor
**Files:** `src/codetrail/extract/python.py`, `tests/unit/test_python_extractor.py`. Dependencies: `tree-sitter`, `tree-sitter-python` (decision 9).
Projects from `pyproject.toml` (`project:<dir>`, attributes `name`, `requires_python`); packages from `[project].dependencies` and `[dependency-groups]` (`package:pypi/<normalised name>`; relation project `depends_on` package with `specifier` and `group`). Modules for `.py` files under a project root (nearest `pyproject.toml` above, `src/` stripped; `__init__.py` names its package); `contains` from project to module. Imports via tree-sitter (`import_statement`, `import_from_statement`, relative levels resolved against the module's package), resolved to the project's modules first (`a.b.c`, then `a.b`, then `a`), then to a declared package by top-level name (normalised, with a small table of known differences such as `yaml` → `pyyaml`), dropping standard-library modules (`sys.stdlib_module_names`); anything else is unresolved.
**Tests:** a `src/` layout and a flat layout; relative imports at two levels; `from pkg import submodule`; a syntax error file still yields its module and a warning-free partial parse; stdlib dropped; third-party mapped to the declared package; files outside any project ignored.

### Task 5: The adr extractor
**Files:** `src/codetrail/extract/adr.py`, `tests/unit/test_adr_extractor.py`.
Handles files matching the target's `[adr] paths` globs. Number from the heading `# 0023. Title` (or the file name `0023-title.md`); title from the heading; status from a `Status:` line (list item or plain) or the first line under `## Status`, lower-cased up to the first space or parenthesis; date from a `Date:` line. `superseded by NNNN` in the status gives `decision:ADR-NNNN supersedes decision:ADR-this`. Files without a number (README, template) are skipped.
**Tests:** a `Status:` line format; a Nygard-style `## Status` section; superseded; template and README skipped.

### Task 6: The update command
**Files:** modify `src/codetrail/config.py` (`TargetConfig.extractors: list[str] = ["python", "adr"]`, `adr: AdrSettings(paths=["docs/adr/*.md"])`), `src/codetrail/repo/refresh.py` (lock taken by callers), `src/codetrail/cli.py`; create `src/codetrail/update.py`, `tests/e2e/test_update_command.py`.
`run_update(paths, name) -> UpdateResult` locks, refreshes the sources, runs the enabled extractors, records the snapshot in `data/<name>/codetrail.db`. The CLI prints the commit, per-kind counts of added/changed/removed facts, unresolved references and warnings. An unknown extractor name in configuration is refused at load.
**Tests:** a fixture project updated, then a commit adding a module and changing a dependency, then an update with no change; the target unchanged throughout.

### Task 7: Documents, the first test repository, finish
README (Phase 2 done, `update` prints facts), spec 5.1 (`prepare`), then `codetrail update shop` with the read-only check, `just ci`, security review, finish.
