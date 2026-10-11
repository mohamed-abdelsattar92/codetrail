"""The swift extractor: packages, targets, their dependencies, and imports per target (design section 5.2)."""

from pathlib import Path

from codetrail.extract import run_extractors
from codetrail.extract.swift import SwiftExtractor
from codetrail.facts import EntityKind, RelationKind

FEATURES = """// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Features",
    platforms: [.iOS(.v18)],
    dependencies: [
        .package(path: "../Core"),
        .package(url: "https://github.com/groue/GRDB.swift", exact: "7.11.1"),
    ],
    targets: [
        .target(name: "Recording"),
        .target(
            name: "LocalStore",
            dependencies: ["Core", .product(name: "GRDB", package: "GRDB.swift")],
            resources: [.copy("Migrations")]
        ),
        .target(name: "Library", dependencies: ["LocalStore", "Recording"]),
        .target(name: "Support", dependencies: ["Core"], path: "Tests/Support"),
        .testTarget(name: "LibraryTests", dependencies: ["Library", "Support"]),
    ]
)
"""
CORE = 'let package = Package(name: "Core", targets: [.target(name: "Core")])\n'


def run(root: Path, files: dict[str, str]):  # type: ignore[no-untyped-def]
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return run_extractors(root, sorted(files), [SwiftExtractor()])


FILES = {
    "ios/Packages/Features/Package.swift": FEATURES,
    "ios/Packages/Core/Package.swift": CORE,
    "ios/Packages/Features/Sources/Library/LibraryView.swift": (
        "import SwiftUI\nimport LocalStore\n@testable import Recording\n"
    ),
    "ios/Packages/Features/Sources/LocalStore/Store.swift": "import Foundation\nimport struct GRDB.Row\nimport Core\n",
    "ios/Packages/Features/Tests/Support/Fake.swift": "import Core\n",
    "ios/Packages/Features/Tests/LibraryTests/LibraryTests.swift": "import XCTest\n@testable import Library\n",
    "ios/App/AppMain.swift": "import SwiftUI\n",
}


def test_packages_targets_and_dependencies(tmp_path: Path) -> None:
    extraction = run(tmp_path, FILES)
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["project:ios/Packages/Features"].attributes == {"name": "Features", "language": "swift"}
    store = entities["swift_target:ios/Packages/Features/LocalStore"]
    assert store.kind is EntityKind.SWIFT_TARGET
    assert store.attributes == {"name": "LocalStore", "kind": "target", "package": "Features",
                                "path": "ios/Packages/Features/Sources/LocalStore"}  # fmt: skip
    assert entities["swift_target:ios/Packages/Features/LibraryTests"].attributes["kind"] == "test"
    assert (
        entities["swift_target:ios/Packages/Features/Support"].attributes["path"]
        == "ios/Packages/Features/Tests/Support"
    )
    assert entities["package:swift/grdb.swift"].attributes == {"url": "https://github.com/groue/GRDB.swift"}
    edges = {(r.source_id, r.kind, r.target_id) for r in extraction.relations}
    library, local_store = "swift_target:ios/Packages/Features/Library", "swift_target:ios/Packages/Features/LocalStore"
    core = "swift_target:ios/Packages/Core/Core"
    assert (library, RelationKind.DEPENDS_ON, local_store) in edges
    assert (local_store, RelationKind.DEPENDS_ON, core) in edges
    assert (local_store, RelationKind.DEPENDS_ON, "package:swift/grdb.swift") in edges
    assert ("project:ios/Packages/Features", RelationKind.CONTAINS, library) in edges
    assert ("project:ios/Packages/Features", RelationKind.DEPENDS_ON, "package:swift/grdb.swift") in edges


def test_imports_belong_to_their_target(tmp_path: Path) -> None:
    extraction = run(tmp_path, FILES)
    imports = {(r.source_id, r.target_id) for r in extraction.relations if r.kind is RelationKind.IMPORTS}
    assert ("swift_target:ios/Packages/Features/Library", "swift_target:ios/Packages/Features/LocalStore") in imports
    assert ("swift_target:ios/Packages/Features/Library", "swift_target:ios/Packages/Features/Recording") in imports
    assert ("swift_target:ios/Packages/Features/LocalStore", "swift_target:ios/Packages/Core/Core") in imports
    assert ("swift_target:ios/Packages/Features/LocalStore", "package:swift/grdb.swift") in imports
    assert ("swift_target:ios/Packages/Features/LibraryTests", "swift_target:ios/Packages/Features/Library") in imports
    # SwiftUI, Foundation and XCTest are Apple's: counted once each, never per file
    assert extraction.unresolved == {"swift": 3}


def test_a_name_prefers_the_target_beside_it_then_the_first(tmp_path: Path) -> None:
    files = {
        "p/Package.swift": '.target(name: "Shared")',
        "q/Package.swift": '.target(name: "Shared"), .target(name: "App", dependencies: ["Shared"])',
        "r/Package.swift": '.target(name: "Tool", dependencies: ["Shared"])',
    }
    edges = {(r.source_id, r.target_id) for r in run(tmp_path, files).relations if r.kind is RelationKind.DEPENDS_ON}
    assert ("swift_target:q/App", "swift_target:q/Shared") in edges
    assert ("swift_target:r/Tool", "swift_target:p/Shared") in edges
    assert ("swift_target:q/App", "swift_target:p/Shared") not in edges


def test_a_file_belongs_to_the_deepest_target_folder(tmp_path: Path) -> None:
    manifest = '.target(name: "Outer"), .target(name: "Inner", path: "Sources/Outer/Inner"), .target(name: "Lib")'
    files = {"P/Package.swift": manifest, "P/Sources/Outer/Inner/File.swift": "import Lib\n"}
    imports = {(r.source_id, r.target_id) for r in run(tmp_path, files).relations if r.kind is RelationKind.IMPORTS}
    assert imports == {("swift_target:P/Inner", "swift_target:P/Lib")}


def test_a_broken_manifest_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"Pkg/Package.swift": 'let package = Package(name: "X", targets: [.target(name: '})
    assert "project:Pkg" in {entity.id for entity in extraction.entities}


def test_hostile_manifests_stay_linear(tmp_path: Path) -> None:
    """A committed Package.swift can't stall the update (Phase 7 review, finding 1)."""
    import time

    hostile = {
        "a/Package.swift": ".target(" * 120_000,
        "b/Package.swift": '.package(url: "a"' * 55_000,
        "c/Package.swift": '.target(name: "A", ' * 50_000 + ")" * 50_000,
        "d/Package.swift": '.target(name: "A", dependencies: [.product(name: "P", package: "Q"' * 15_000,
    }
    started = time.process_time()
    extraction = run(tmp_path, hostile)
    # About 0.7 s on a laptop and 2.2 s on CI's runner beside other workers. Measured on the laptop with each
    # safeguard undone, the slowest input took 108 s without REMOTE_PACKAGE's bound and over 120 s without the
    # nested-call skip or with per-call bracket matching, so 10 s catches each with room for a slow machine.
    assert time.process_time() - started < 10
    assert {"project:a", "project:b", "project:c", "project:d"} <= {entity.id for entity in extraction.entities}


def test_hostile_dependency_lists_stay_linear(tmp_path: Path) -> None:
    """A target's dependencies list can't stall the update: PRODUCT's and NAMED_TARGET's tails are bounded."""
    import time

    # Every bracket closes, so each target's ~0.93 MB dependencies list is read, with no ")" in it for PRODUCT
    # (e, f) or NAMED_TARGET (g) to stop at. Each file stays under the extractor's 1,000,000-byte cap; PRODUCT's
    # cost without its bound is quadratic in one list, so a second file doubles the margin over the budget.
    products = '.target(name: "A", dependencies: [' + '.product(name: "P", package: "Q"' * 29_000 + "]" + ")" * 29_001
    hostile = {
        "e/Package.swift": products,
        "f/Package.swift": products,
        "g/Package.swift": '.target(name: "A", dependencies: [' + '.byName(name: "A"' * 55_000 + "]" + ")" * 55_001,
    }
    started = time.process_time()
    extraction = run(tmp_path, hostile)
    # About 1 s on a laptop (1.7 s while it was heavily loaded); with a bound undone, measured on the laptop by
    # patching the loaded module, this test took 36-45 s without PRODUCT's and 35-41 s without NAMED_TARGET's.
    # At 15,000 calls a list took only 6 s and 3 s, which is why the files are near the cap.
    assert time.process_time() - started < 10
    # Each target call was read, so its dependencies list reached PRODUCT and NAMED_TARGET.
    assert {f"swift_target:{folder}/A" for folder in "efg"} <= {entity.id for entity in extraction.entities}


# 40,000 uniquely named targets in one manifest, about 880 KB, under the extractor's 1,000,000-byte cap.
MANY_TARGETS = "".join(f'.target(name:"T{number:05d}")' for number in range(40_000))


def test_hostile_target_names_resolve_linearly(tmp_path: Path) -> None:
    """Many dependency names against many targets can't stall the update: targets are looked up by name."""
    import time

    # One target depends on 90,000 names (about 810 KB); the first 40,000 name a target in the other manifest.
    names = "".join(f'"T{number:05d}",' for number in range(90_000))
    hostile = {"h/Package.swift": f'.target(name: "A", dependencies: [{names}])', "i/Package.swift": MANY_TARGETS}
    started = time.process_time()
    extraction = run(tmp_path, hostile)
    # About 1.2 s on a laptop. Measured there when each name scanned every target, it took 111 s (3.6 billion
    # comparisons), so 10 s catches a scan with room for a slow machine.
    assert time.process_time() - started < 10
    edges = {(r.source_id, r.kind, r.target_id) for r in extraction.relations}
    assert ("swift_target:h/A", RelationKind.DEPENDS_ON, "swift_target:i/T39999") in edges
    assert extraction.unresolved == {"swift": 50_000}


def test_hostile_imports_find_their_target_linearly(tmp_path: Path) -> None:
    """Many imports beside many targets can't stall the update: a file's target is found from its folders."""
    import time

    # One file of 65,000 imports (about 910 KB) in the first of 40,000 targets; the first 40,000 name a target.
    imports = "".join(f"import T{number:05d}\n" for number in range(65_000))
    hostile = {"i/Package.swift": MANY_TARGETS, "i/Sources/T00000/Hostile.swift": imports}
    started = time.process_time()
    extraction = run(tmp_path, hostile)
    # About 1.2 s on a laptop (3 s while it was heavily loaded). Measured there when each import's file scanned
    # every target's folder, it took 163 s (2.6 billion comparisons), so 10 s catches a scan with room to spare.
    assert time.process_time() - started < 10
    edges = {(r.source_id, r.kind, r.target_id) for r in extraction.relations}
    assert ("swift_target:i/T00000", RelationKind.IMPORTS, "swift_target:i/T39999") in edges
    assert extraction.unresolved == {"swift": 25_000}


def test_hostile_deep_files_find_their_target_linearly(tmp_path: Path) -> None:
    """A deeply nested file with many imports can't stall the update: its folders are walked once per file."""
    import time

    # Two files 400 folders deep (about 800 characters, under the 1,024 a macOS path can hold with the test's folder)
    # in one target, each with 65,000 imports (about 910 KB).
    deep = "d/" * 400
    imports = "".join(f"import T{number:05d}\n" for number in range(65_000))
    hostile = {
        "Package.swift": '.target(name: "A", path: "A")',
        f"A/{deep}One.swift": imports,
        f"A/{deep}Two.swift": imports,
    }
    started = time.process_time()
    extraction = run(tmp_path, hostile)
    # Measured on a laptop: resolving one such file took 0.06 s with the walk once per file, and 6.7 s when each
    # import walked the 400 folders again, so the two files together took 16 s against this 10 s budget.
    assert time.process_time() - started < 10
    assert extraction.unresolved == {"swift": 65_000}


def test_hostile_long_target_names_resolve_linearly(tmp_path: Path) -> None:
    """A target with a very long name and many dependencies can't stall the update."""
    import time

    # A 480 KB name, dropped as a fact for its overlong id, with 120,000 dependencies (about 480 KB).
    manifest = f'.target(name: "{"N" * 480_000}", dependencies: [{'"a",' * 120_000}])'
    started = time.process_time()
    extraction = run(tmp_path, {"Package.swift": manifest})
    # About 0.4 s on a laptop. Measured there when each dependency split the 480 KB id again, it took 22 s.
    assert time.process_time() - started < 10
    assert any("skipped a fact whose id is longer" in warning for warning in extraction.warnings)
    assert extraction.relations == []


def test_package_urls_lose_their_credentials(tmp_path: Path) -> None:
    url = "https://me:hunter2@git.example/Lib.git"
    manifest = f'let package = Package(name: "X", dependencies: [.package(url: "{url}", from: "1.0.0")])'
    extraction = run(tmp_path, {"X/Package.swift": manifest})
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["package:swift/lib"].attributes == {"url": "https://git.example/Lib.git"}


def test_scp_style_urls_lose_their_credentials(tmp_path: Path) -> None:
    url = "me:hunter2@git.example:team/Lib.git"
    manifest = f'let package = Package(name: "X", dependencies: [.package(url: "{url}", from: "1.0.0")])'
    extraction = run(tmp_path, {"X/Package.swift": manifest})
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["package:swift/lib"].attributes == {"url": "git.example:team/Lib.git"}
