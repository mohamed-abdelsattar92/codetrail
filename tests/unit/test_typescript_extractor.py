"""The typescript extractor: modules, imports, npm projects, Astro routes and Workers (design section 17.1)."""

import json
from pathlib import Path

import pytest

from codetrail.extract import Extraction, run_extractors
from codetrail.extract.typescript import TypeScriptExtractor, strip_json_comments
from codetrail.facts import EntityKind, RelationKind

ROOT_PACKAGE = json.dumps({"name": "shop", "private": True, "workspaces": ["apps/*", "packages/*"]})
SITE_PACKAGE = json.dumps({
    "name": "@shop/site",
    "dependencies": {"astro": "^5.0.0", "@shop/ui": "workspace:*", "zod": "^3"},
    "devDependencies": {"wrangler": "^4"},
})  # fmt: skip
UI_PACKAGE = json.dumps({"name": "@shop/ui", "peerDependencies": {"react": "^19"}})
TSCONFIG = """{
  // comments and trailing commas, as tsconfig allows
  "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["src/*"], }, },
}"""
WRANGLER = """{
  // the landing Worker
  "name": "shop-site",
  "main": "worker/index.ts",
  "d1_databases": [{ "binding": "DB", "database_name": "waitlist", "database_id": "secret-looking-id" }],
  "kv_namespaces": [{ "binding": "CACHE", "id": "abc" }],
  "r2_buckets": [{ "binding": "ASSETS", "bucket_name": "assets" }],
  "routes": [{ "pattern": "shop.example/*", "zone_name": "shop.example" }],
  "vars": { "API_TOKEN": "do-not-read-this-value" },
  "define": { "BUILD_SECRET": "define-value" },
  "hyperdrive": [{ "binding": "HD", "id": "hd-id", "localConnectionString": "postgres://user:pw@host/db" }],
  "env": { "production": { "vars": { "PROD_KEY": "prod-value" }, "d1_databases": [{ "binding": "PRODDB" }] } },
}"""
PAGE = """---
import Layout from "../layouts/Layout.astro";
import { Button } from "@shop/ui";
const title = "Home";
---
<Layout title={title}><Button /></Layout>
<script>
  import { track } from "@/lib/analytics";
  track("home");
</script>
"""
ENDPOINT = """import { z } from "zod";
import type { Env } from "../../env";
export async function POST({ request }) { return new Response("ok"); }
export const GET = () => new Response("list");
"""
FILES = {
    "package.json": ROOT_PACKAGE,
    "apps/site/package.json": SITE_PACKAGE,
    "apps/site/astro.config.mjs": 'import { defineConfig } from "astro/config";\nexport default defineConfig({});\n',
    "apps/site/tsconfig.json": TSCONFIG,
    "apps/site/wrangler.jsonc": WRANGLER,
    "apps/site/worker/index.ts": 'import { handle } from "./handle.js";\nimport fs from "node:fs";\n',
    "apps/site/worker/handle.ts": "export const handle = () => 1;\n",
    "apps/site/src/pages/index.astro": PAGE,
    "apps/site/src/pages/blog/[slug].md": "# Post\n",
    "apps/site/src/pages/docs/[...rest].astro": "---\n---\n<p>docs</p>\n",
    "apps/site/src/pages/api/waitlist.ts": ENDPOINT,
    "apps/site/src/layouts/Layout.astro": "---\nconst { title } = Astro.props;\n---\n<html>{title}</html>\n",
    "apps/site/src/lib/analytics.ts": 'const lazy = await import("./lazy");\nexport function track(x: string) {}\n',
    "apps/site/src/lib/lazy.ts": "export default 1;\n",
    "apps/site/src/env.d.ts": "declare const x: string;\n",
    "apps/site/dist/index.js": 'import "./chunk";\n',
    "packages/ui/package.json": UI_PACKAGE,
    "packages/ui/index.tsx": 'export { Button } from "./Button";\nconst React = require("react");\n',
    "packages/ui/Button.tsx": "export const Button = () => <button />;\n",
    "packages/ui/vendor.min.js": "!function(){}();\n",
    "packages/ui/broken.ts": "import { from 'nowhere\n",
}


def run(root: Path, files: dict[str, str]) -> Extraction:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return run_extractors(root, sorted(files), [TypeScriptExtractor()])


@pytest.fixture
def extraction(tmp_path: Path) -> Extraction:
    return run(tmp_path, FILES)


def ids(extraction: Extraction, kind: EntityKind) -> set[str]:
    return {entity.id for entity in extraction.entities if entity.kind is kind}


def edges(extraction: Extraction, kind: RelationKind) -> set[tuple[str, str]]:
    return {(relation.source_id, relation.target_id) for relation in extraction.relations if relation.kind is kind}


def test_package_json_files_are_projects_with_their_dependencies(extraction: Extraction) -> None:
    assert ids(extraction, EntityKind.PROJECT) == {"project:.", "project:apps/site", "project:packages/ui"}
    depends = {(r.source_id, r.target_id, r.attributes.get("group")) for r in extraction.relations
               if r.kind is RelationKind.DEPENDS_ON}  # fmt: skip
    assert ("project:apps/site", "package:npm/astro", "main") in depends
    assert ("project:apps/site", "package:npm/wrangler", "dev") in depends
    assert ("project:packages/ui", "package:npm/react", "peer") in depends
    assert ("project:apps/site", "project:packages/ui", "main") in depends  # a workspace sibling is a project


def test_imports_resolve_like_typescript(extraction: Extraction) -> None:
    imports = edges(extraction, RelationKind.IMPORTS)
    assert ("module:apps/site/worker/index.ts", "module:apps/site/worker/handle.ts") in imports  # "./handle.js"
    assert ("module:apps/site/src/pages/index.astro", "module:apps/site/src/layouts/Layout.astro") in imports
    assert ("module:apps/site/src/pages/index.astro", "project:packages/ui") in imports  # workspace package
    assert ("module:apps/site/src/pages/index.astro", "module:apps/site/src/lib/analytics.ts") in imports  # paths
    assert ("module:apps/site/src/lib/analytics.ts", "module:apps/site/src/lib/lazy.ts") in imports  # import()
    assert ("module:apps/site/src/pages/api/waitlist.ts", "package:npm/zod") in imports
    assert ("module:packages/ui/index.tsx", "module:packages/ui/Button.tsx") in imports  # export ... from
    assert ("module:packages/ui/index.tsx", "package:npm/react") in imports  # require
    assert not any(target.endswith("node:fs") or target == "package:npm/fs" for _, target in imports)


def test_import_lines_are_kept_for_astro_blocks(extraction: Extraction) -> None:
    [relation] = [r for r in extraction.relations if r.kind is RelationKind.IMPORTS
                  and r.target_id == "module:apps/site/src/lib/analytics.ts"]  # fmt: skip
    assert relation.sources[0].start_line == 8


def test_astro_pages_and_endpoints_are_routes(extraction: Extraction) -> None:
    assert ids(extraction, EntityKind.ROUTE) == {
        "route:apps/site GET /",
        "route:apps/site GET /blog/{slug}",
        "route:apps/site GET /docs/{rest}",
        "route:apps/site POST /api/waitlist",
        "route:apps/site GET /api/waitlist",
    }
    assert ("project:apps/site", "route:apps/site POST /api/waitlist") in edges(extraction, RelationKind.CONTAINS)


def test_a_worker_keeps_binding_names_and_never_values(extraction: Extraction) -> None:
    [worker] = [entity for entity in extraction.entities if entity.kind is EntityKind.WORKER]
    assert worker.id == "worker:apps/site"
    assert worker.attributes["name"] == "shop-site"
    assert worker.attributes["main"] == "apps/site/worker/index.ts"
    assert set(worker.attributes["bindings"]) == {"d1:DB", "kv:CACHE", "r2:ASSETS", "route:shop.example/*"}
    every_fact = json.dumps([[e.id, dict(e.attributes)] for e in extraction.entities]
                            + [[r.key, dict(r.attributes)] for r in extraction.relations])  # fmt: skip
    for secret in (
        "do-not-read-this-value",
        "API_TOKEN",
        "secret-looking-id",
        "define-value",
        "postgres://",
        "prod-value",
        "PROD_KEY",
        "hd-id",
        "abc",
    ):
        assert secret not in every_fact
    assert ("worker:apps/site", "module:apps/site/worker/index.ts") in edges(extraction, RelationKind.CONTAINS)


def test_generated_declaration_and_minified_files_are_skipped(extraction: Extraction) -> None:
    modules = ids(extraction, EntityKind.MODULE)
    for skipped in ("apps/site/src/env.d.ts", "apps/site/dist/index.js", "packages/ui/vendor.min.js"):
        assert f"module:{skipped}" not in modules


def test_an_unparseable_file_is_still_a_module_and_breaks_nothing(extraction: Extraction) -> None:
    assert "module:packages/ui/broken.ts" in ids(extraction, EntityKind.MODULE)
    assert not [warning for warning in extraction.warnings if "broken.ts" not in warning]


def test_wrangler_toml_is_read_too(tmp_path: Path) -> None:
    toml = (
        'name = "api"\nmain = "src/index.ts"\nkv_namespaces = [{ binding = "SESSIONS", id = "x" }]\n[vars]\nKEY = "v"\n'
    )
    found = run(tmp_path, {"workers/api/wrangler.toml": toml, "workers/api/src/index.ts": "export default {};\n"})
    [worker] = [entity for entity in found.entities if entity.kind is EntityKind.WORKER]
    assert worker.attributes["bindings"] == ["kv:SESSIONS"]
    assert "KEY" not in json.dumps(dict(worker.attributes))


def test_json_comments_are_stripped_outside_strings() -> None:
    text = '{"a": "http://x//y", /* c */ "b": [1, 2,], // d\n}'
    assert json.loads(strip_json_comments(text)) == {"a": "http://x//y", "b": [1, 2]}


def test_a_lone_module_without_a_package_json_is_still_read(tmp_path: Path) -> None:
    found = run(
        tmp_path, {"scripts/build.mjs": 'import { a } from "./a.mjs";\n', "scripts/a.mjs": "export const a = 1;\n"}
    )
    assert ("module:scripts/build.mjs", "module:scripts/a.mjs") in edges(found, RelationKind.IMPORTS)


def test_an_oversized_binding_name_is_cut(tmp_path: Path) -> None:
    toml = f'name = "w"\n[[kv_namespaces]]\nbinding = "{"X" * 5000}"\nid = "y"\n'
    found = run(tmp_path, {"w/wrangler.toml": toml})
    [worker] = [entity for entity in found.entities if entity.kind is EntityKind.WORKER]
    assert all(len(binding) <= 300 for binding in worker.attributes["bindings"])


def test_escaping_absolute_and_extends_paths_are_never_followed(tmp_path: Path) -> None:
    tsconfig = json.dumps(
        {
            "extends": "../../../../etc/tsconfig.json",
            "compilerOptions": {"baseUrl": "../../..", "paths": {"x/*": ["../*"]}},
        }
    )
    found = run(tmp_path, {
        "app/package.json": '{"name": "app"}', "app/tsconfig.json": tsconfig,
        "app/a.ts": 'import "../../../outside";\nimport "/etc/passwd";\nimport "x/y";\n',
    })  # fmt: skip
    assert edges(found, RelationKind.IMPORTS) == set()
    assert found.unresolved.get("typescript", 0) == 3


def test_a_hostile_tsconfig_breaks_nothing(tmp_path: Path) -> None:
    files = {
        "app/package.json": '{"name": "app"}',
        "app/tsconfig.json": '{"compilerOptions": {"paths": 7}}',
        "app/a.ts": 'import "./b";\n',
        "app/b.ts": "",
    }
    found = run(tmp_path, files)
    assert ("module:app/a.ts", "module:app/b.ts") in edges(found, RelationKind.IMPORTS)


def test_asset_imports_are_neither_modules_nor_unresolved(tmp_path: Path) -> None:
    code = 'import icons from "./icons.json";\nimport logo from "../a/logo.svg?raw";\nimport "./global.css";\n'
    found = run(tmp_path, {"site/src/x.ts": code, "site/src/icons.json": "{}"})
    assert edges(found, RelationKind.IMPORTS) == set()
    assert found.unresolved.get("typescript", 0) == 0


def test_many_unclosed_script_tags_are_read_in_linear_time(tmp_path: Path) -> None:
    import time

    hostile = "---\n---\n" + "<script>x" * 40_000  # every opening would rescan the rest with a lazy regex
    started = time.monotonic()
    found = run(tmp_path, {"site/src/pages/x.astro": hostile})
    assert time.monotonic() - started < 2
    assert "module:site/src/pages/x.astro" in ids(found, EntityKind.MODULE)


def test_scripts_are_found_case_insensitively_with_their_lines(tmp_path: Path) -> None:
    page = '---\n---\n<p>a</p>\n<SCRIPT type="module">\nimport "./b";\n</Script>\n'
    found = run(tmp_path, {"site/a.astro": page, "site/b.ts": ""})
    [relation] = [r for r in found.relations if r.kind is RelationKind.IMPORTS]
    assert (relation.target_id, relation.sources[0].start_line) == ("module:site/b.ts", 5)


def test_a_tsconfig_with_too_many_paths_is_skipped_with_a_warning(tmp_path: Path) -> None:
    paths = {f"p{index}/*": ["src/*"] for index in range(2000)}
    files = {
        "app/package.json": '{"name": "app"}',
        "app/tsconfig.json": json.dumps({"compilerOptions": {"paths": paths}}),
        "app/a.ts": 'import "./b";\n',
        "app/b.ts": "",
    }
    found = run(tmp_path, files)
    assert any("app/tsconfig.json" in warning for warning in found.warnings)
    assert ("module:app/a.ts", "module:app/b.ts") in edges(found, RelationKind.IMPORTS)


def test_credentials_in_dependency_specifiers_are_never_stored(tmp_path: Path) -> None:
    package = {"name": "app", "dependencies": {"private": "git+https://user:s3cret@git.example/org/repo.git#v1",
                                                "scp": "git@user:token@host:org/repo.git"}}  # fmt: skip
    found = run(tmp_path, {"app/package.json": json.dumps(package)})
    stored = json.dumps([dict(relation.attributes) for relation in found.relations])
    assert "s3cret" not in stored and "token" not in stored
    assert "git.example/org/repo.git" in stored


def test_many_closed_scripts_are_read_quickly(tmp_path: Path) -> None:
    import time

    page = "---\n---\n" + "<script>\n</script>\n" * 60_000
    started = time.monotonic()
    run(tmp_path, {"site/x.astro": page})
    assert time.monotonic() - started < 3


def test_absolute_worker_entries_and_base_urls_are_dropped(tmp_path: Path) -> None:
    files = {
        "w/wrangler.json": '{"name": "w", "main": "/etc/x.ts"}',
        "w/package.json": '{"name": "w"}',
        "w/tsconfig.json": '{"compilerOptions": {"baseUrl": "/", "paths": {"@/*": ["/etc/*"]}}}',
        "w/a.ts": 'import "@/x";\n',
        "w/etc/x.ts": "",
    }
    found = run(tmp_path, files)
    [worker] = [entity for entity in found.entities if entity.kind is EntityKind.WORKER]
    assert "main" not in worker.attributes
    assert edges(found, RelationKind.IMPORTS) == set()


def test_empty_target_lists_count_towards_the_tsconfig_cap(tmp_path: Path) -> None:
    paths: dict[str, list[str]] = {f"a{index}": [] for index in range(2000)}
    files = {
        "app/package.json": '{"name": "app"}',
        "app/tsconfig.json": json.dumps({"compilerOptions": {"paths": paths}}),
    }
    found = run(tmp_path, files)
    assert any("app/tsconfig.json" in warning for warning in found.warnings)
