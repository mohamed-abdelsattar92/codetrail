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
    assert time.process_time() - started < 2
    assert {"project:a", "project:b", "project:c", "project:d"} <= {entity.id for entity in extraction.entities}


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
