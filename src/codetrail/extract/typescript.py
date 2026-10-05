"""The typescript extractor: TypeScript, JavaScript and Astro modules, npm projects, Astro routes and Workers (17.1).

Every `package.json` marks a project; a source file belongs to the nearest project above it. Imports are read with
tree-sitter (the TypeScript, TSX and JavaScript grammars), which tolerates syntax errors; `.astro` files contribute the
code between their `---` fences and in their `<script>` elements. Relative imports resolve the way TypeScript does,
`tsconfig.json` `paths` included; a bare name resolves to a workspace project, else to a declared npm package.
Wrangler configs become Workers with the names of their bindings; `vars` values are never read.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from codetrail.extract import FileFacts, Reference, Resolution, without_credentials
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

TYPESCRIPT = Language(tree_sitter_typescript.language_typescript())
TSX = Language(tree_sitter_typescript.language_tsx())
JAVASCRIPT = Language(tree_sitter_javascript.language())
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
RESOLVE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".astro")
ASSET_SUFFIXES = {
    ".json",
    ".css",
    ".scss",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".woff2",
    ".md",
    ".mdx",
    ".txt",
}
SKIPPED_SUFFIXES = (".d.ts", ".d.mts", ".d.cts", ".min.js", ".min.mjs")
SKIPPED_FOLDERS = {"dist", "build", ".astro", "node_modules"}
WRANGLER = {"wrangler.toml", "wrangler.json", "wrangler.jsonc"}
ASTRO_CONFIG = re.compile(r"astro\.config\.(mjs|js|ts|mts|cjs)")
PAGE_SUFFIXES = (".astro", ".md", ".mdx")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ALL")
FENCE = re.compile(rb"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", re.S)
GROUPS = {"dependencies": "main", "devDependencies": "dev", "peerDependencies": "peer"}
# Wrangler binding tables, and the key that names each binding.
BINDINGS = {
    "d1_databases": ("d1", "binding"), "kv_namespaces": ("kv", "binding"), "r2_buckets": ("r2", "binding"),
    "queues.producers": ("queue", "binding"), "services": ("service", "binding"),
    "durable_objects.bindings": ("durable_object", "name"), "analytics_engine_datasets": ("analytics", "binding"),
}  # fmt: skip
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "crypto", "dns", "events", "fs", "http", "https", "net", "os", "path",
    "process", "querystring", "readline", "stream", "string_decoder", "timers", "tls", "url", "util", "worker_threads",
    "zlib",
}  # fmt: skip


def project_id(folder: str) -> str:
    return f"project:{folder or '.'}"


def strip_json_comments(text: str) -> str:
    """JSON with comments (`//`, `/* */`) and trailing commas, as Wrangler and tsconfig allow, made plain JSON."""
    out: list[str] = []
    index, in_string = 0, False
    while index < len(text):
        character = text[index]
        if in_string:
            out.append(character)
            if character == "\\" and index + 1 < len(text):
                out.append(text[index + 1])
                index += 1
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
            out.append(character)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        else:
            out.append(character)
        index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _skipped(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return path.endswith(SKIPPED_SUFFIXES) or any(part in SKIPPED_FOLDERS for part in parts[:-1])


def _folder(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def _join(folder: str, relative: str) -> str | None:
    """`folder/relative` normalized, or None when it is absolute or climbs above the repository."""
    if relative.startswith("/"):
        return None
    parts: list[str] = [part for part in folder.split("/") if part]
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


class TypeScriptExtractor:
    name = "typescript"
    version = 1

    def __init__(self, max_tsconfig_paths: int = 100) -> None:
        self._max_paths = max_tsconfig_paths
        self._roots: list[str] = []
        self._astro_roots: set[str] = set()
        self._tsconfigs: dict[str, tuple[str, dict[str, list[str]]]] = {}  # folder -> (base folder, paths)
        self._parsers = {language: Parser(language) for language in (TYPESCRIPT, TSX, JAVASCRIPT)}

    def handles(self, path: str) -> bool:
        if _skipped(path):
            return False
        name = PurePosixPath(path).name
        if name in ("package.json", "tsconfig.json") or name in WRANGLER:
            return True
        if path.endswith(SOURCE_SUFFIXES) or path.endswith(".astro"):
            return True
        return path.endswith((".md", ".mdx")) and "/src/pages/" in f"/{path}"

    def prepare(self, paths: Sequence[str]) -> None:
        roots = {_folder(path) for path in paths if PurePosixPath(path).name == "package.json"}
        self._roots = sorted(roots, key=len, reverse=True)  # deepest first: a nested project wins
        self._astro_roots = {_folder(path) for path in paths if ASTRO_CONFIG.fullmatch(PurePosixPath(path).name)}
        self._tsconfigs = {}

    def _root_of(self, path: str) -> str | None:
        for root in self._roots:
            if not root or path.startswith(root + "/"):
                return root
        return None

    def extract(self, path: str, content: bytes) -> FileFacts:
        name = PurePosixPath(path).name
        if name == "package.json":
            return self._extract_project(path, content)
        if name == "tsconfig.json":
            self._read_tsconfig(path, content)
            return FileFacts(path)
        if name in WRANGLER:
            return self._extract_worker(path, content)
        if path.endswith((".md", ".mdx")):
            return self._extract_page(path)
        return self._extract_module(path, content)

    def _extract_project(self, path: str, content: bytes) -> FileFacts:
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, dict):
            return FileFacts(path)
        project = project_id(_folder(path))
        attributes = {"name": data["name"]} if isinstance(data.get("name"), str) and data["name"] else {}
        entities = [Entity(project, EntityKind.PROJECT, attributes, (Source(path),))]
        references = []
        for field, group in GROUPS.items():
            table = data.get(field)
            if not isinstance(table, dict):
                continue
            for package_name, specifier in sorted(table.items()):
                if not isinstance(package_name, str) or not package_name:
                    continue
                package = f"package:npm/{package_name}"
                entities.append(Entity(package, EntityKind.PACKAGE, {}, (Source(path),)))
                dependency = {"specifier": without_credentials(str(specifier)), "group": group}
                references.append(Reference(project, RelationKind.DEPENDS_ON, package, (Source(path),), dependency))
        return FileFacts(path, tuple(entities), tuple(references))

    def _read_tsconfig(self, path: str, content: bytes) -> None:
        data = json.loads(strip_json_comments(content.decode("utf-8")))
        options = data.get("compilerOptions", {}) if isinstance(data, dict) else {}
        if not isinstance(options, dict):
            return
        folder = _folder(path)
        base = _join(folder, str(options.get("baseUrl", "."))) or folder
        paths = options.get("paths", {})
        if (
            isinstance(paths, dict)
            and len(paths) + sum(len(v) for v in paths.values() if isinstance(v, list)) > self._max_paths
        ):
            raise ValueError("too many paths")  # patterns and targets both count; real configs have a handful
        if isinstance(paths, dict):
            self._tsconfigs[folder] = (base, {str(key): [str(value) for value in values] for key, values in
                                              paths.items() if isinstance(values, list)})  # fmt: skip

    def _extract_worker(self, path: str, content: bytes) -> FileFacts:
        text = content.decode("utf-8")
        data = tomllib.loads(text) if path.endswith(".toml") else json.loads(strip_json_comments(text))
        if not isinstance(data, dict):
            return FileFacts(path)
        folder = _folder(path)
        worker = f"worker:{folder or '.'}"
        bindings: list[str] = []  # flat "type:name" strings, so the attribute length cut applies
        for key, (kind, field) in BINDINGS.items():
            table: Any = data
            for part in key.split("."):
                table = table.get(part, []) if isinstance(table, dict) else []
            for entry in table if isinstance(table, list) else []:
                if isinstance(entry, dict) and isinstance(entry.get(field), str):
                    bindings.append(f"{kind}:{entry[field]}")
        routes = data.get("routes") or ([data["route"]] if data.get("route") else [])
        for route in routes if isinstance(routes, list) else []:
            pattern = route.get("pattern") if isinstance(route, dict) else route
            if isinstance(pattern, str):
                bindings.append(f"route:{pattern}")
        attributes: dict[str, Any] = {"bindings": bindings}  # binding names only; `vars` values are never read
        if isinstance(data.get("name"), str):
            attributes["name"] = data["name"]
        references = []
        main = data.get("main")
        if isinstance(main, str) and (entry := _join(folder, main)):
            attributes["main"] = entry
            references.append(Reference(worker, RelationKind.CONTAINS, f"module:{entry}", (Source(path),)))
        return FileFacts(path, (Entity(worker, EntityKind.WORKER, attributes, (Source(path),)),), tuple(references))

    def _route(self, path: str, method: str, line: int | None = None) -> tuple[Entity, Reference] | None:
        root = next((root for root in sorted(self._astro_roots, key=len, reverse=True)
                     if path.startswith(f"{root}/src/pages/" if root else "src/pages/")), None)  # fmt: skip
        if root is None:
            return None
        relative = path.removeprefix(f"{root}/" if root else "").removeprefix("src/pages/")
        segments = []
        for segment in PurePosixPath(relative).with_suffix("").parts:
            segment = re.sub(r"\[\.\.\.(\w+)\]", r"{\1}", segment)
            segment = re.sub(r"\[(\w+)\]", r"{\1}", segment)
            segments.append(segment)
        if segments and segments[-1] == "index":
            segments.pop()
        route_path = "/" + "/".join(segments)
        route = f"route:{root or '.'} {method} {route_path}"
        source = Source(path, line, line) if line else Source(path)
        entity = Entity(route, EntityKind.ROUTE, {"method": method, "path": route_path, "project": project_id(root)},
                        (source,))  # fmt: skip
        return entity, Reference(project_id(root), RelationKind.CONTAINS, route, (source,))

    def _extract_page(self, path: str) -> FileFacts:
        found = self._route(path, "GET")
        return FileFacts(path, (found[0],), (found[1],)) if found else FileFacts(path)

    def _extract_module(self, path: str, content: bytes) -> FileFacts:
        root = self._root_of(path)
        module_id = f"module:{path}"
        attributes = {"name": path, "language": "astro" if path.endswith(".astro") else "typescript"}
        if root is not None:
            attributes["project"] = project_id(root)
        entities = [Entity(module_id, EntityKind.MODULE, attributes, (Source(path),))]
        references = (
            [Reference(project_id(root), RelationKind.CONTAINS, module_id, (Source(path),))] if root is not None else []
        )
        methods: list[tuple[str, int]] = []
        for tree, offset in self._trees(path, content):
            for specifier, line in _imports(tree.root_node):
                source = Source(path, line + offset, line + offset)
                references.append(
                    Reference(module_id, RelationKind.IMPORTS, specifier, (source,), {"specifier": specifier})
                )
            if not path.endswith(".astro"):
                methods += [(method, line + offset) for method, line in _exported_methods(tree.root_node)]
        if path.endswith(".astro"):
            methods = [("GET", 1)]
        for method, line in methods:
            found = self._route(path, method, line)
            if found:
                entities.append(found[0])
                references.append(found[1])
        return FileFacts(path, tuple(entities), tuple(references))

    def _trees(self, path: str, content: bytes) -> Iterator[tuple[Any, int]]:
        """Each parsed code block of the file, with the number of lines before it."""
        if not path.endswith(".astro"):
            language = (
                TSX
                if path.endswith((".tsx", ".jsx"))
                else JAVASCRIPT
                if path.endswith((".js", ".mjs", ".cjs"))
                else TYPESCRIPT
            )
            yield self._parsers[language].parse(content), 0
            return
        blocks = []
        fence = FENCE.match(content)
        if fence:
            blocks.append((fence.group(1), content[: fence.start(1)].count(b"\n")))
        blocks += _scripts(content)
        for block, offset in blocks:
            yield self._parsers[TYPESCRIPT].parse(block), offset

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        ours = self._project_ids()
        names = {
            str(entity.attributes["name"]): entity.id
            for entity in known.values()
            if entity.kind is EntityKind.PROJECT and entity.id in ours and "name" in entity.attributes
        }
        relations: list[Relation] = []
        unresolved = 0
        for file in files:
            for reference in file.references:
                if reference.kind is RelationKind.DEPENDS_ON:
                    name = reference.target.removeprefix("package:npm/")
                    target = names.get(name, reference.target)  # a workspace sibling: a project, not a package
                    relations.append(Relation(reference.source_id, reference.kind, target, reference.attributes,
                                              reference.sources))  # fmt: skip
                    continue
                if reference.kind is not RelationKind.IMPORTS:
                    relations.append(Relation(reference.source_id, reference.kind, reference.target,
                                              reference.attributes, reference.sources))  # fmt: skip
                    continue
                resolved = self._resolve_import(file.path, reference.target, known, names)
                if resolved is None:
                    unresolved += 1
                elif resolved and resolved != reference.source_id:
                    relations.append(
                        Relation(reference.source_id, RelationKind.IMPORTS, resolved, {}, reference.sources)
                    )
        return Resolution(relations, unresolved)

    def _project_ids(self) -> set[str]:
        return {project_id(root) for root in self._roots}

    def _resolve_import(
        self, path: str, specifier: str, known: Mapping[str, Entity], names: Mapping[str, str]
    ) -> str | None:
        """The module, project or package an import names; "" for a Node built-in (not counted); None if unknown."""
        if PurePosixPath(specifier.split("?", 1)[0]).suffix.lower() in ASSET_SUFFIXES:
            return ""  # an asset (JSON, CSS, images): not code, so neither a module nor unresolved
        if specifier.startswith("/"):
            return None  # absolute: never resolved
        if specifier.startswith("."):
            return self._module(_join(_folder(path), specifier), known)
        mapped = self._mapped(path, specifier, known)
        if mapped:
            return mapped
        bare = specifier.removeprefix("node:")
        if specifier.startswith("node:") or bare.split("/")[0] in NODE_BUILTINS:
            return ""
        parts = bare.split("/")
        name = "/".join(parts[:2]) if bare.startswith("@") else parts[0]
        if name in names:
            return names[name]
        package = f"package:npm/{name}"
        return package if package in known else None

    def _mapped(self, path: str, specifier: str, known: Mapping[str, Entity]) -> str | None:
        folder = _folder(path)
        while True:
            if folder in self._tsconfigs:
                base, patterns = self._tsconfigs[folder]
                for pattern, targets in patterns.items():
                    prefix, star, suffix = pattern.partition("*")
                    if star and specifier.startswith(prefix) and specifier.endswith(suffix):
                        middle = specifier[len(prefix) : len(specifier) - len(suffix)]
                    elif not star and specifier == pattern:
                        middle = ""
                    else:
                        continue
                    for target in targets:
                        found = self._module(_join(base, target.replace("*", middle)), known)
                        if found:
                            return found
                return None
            if not folder:
                return None
            folder = _folder(folder)

    @staticmethod
    def _module(candidate: str | None, known: Mapping[str, Entity]) -> str | None:
        if candidate is None:
            return None
        stem = re.sub(r"\.(js|mjs|cjs|jsx)$", "", candidate)  # TypeScript's ESM imports name the emitted .js file
        options = [candidate, *(stem + suffix for suffix in RESOLVE_SUFFIXES),
                   *(f"{candidate}/index{suffix}" for suffix in RESOLVE_SUFFIXES)]  # fmt: skip
        return next((f"module:{option}" for option in options if f"module:{option}" in known), None)


def _scripts(content: bytes) -> list[tuple[bytes, int]]:
    """The code inside each <script> element and the lines before it, found in one pass (no backtracking regex)."""
    lower = content.lower()
    found: list[tuple[bytes, int]] = []
    position = lines = counted = 0
    while (opening := lower.find(b"<script", position)) != -1:
        start = lower.find(b">", opening)
        end = lower.find(b"</script>", start) if start != -1 else -1
        if end == -1:
            break  # no closing tag after this one, so none after any later opening either
        lines += content.count(b"\n", counted, start + 1)  # a running count: each byte counted once
        counted = start + 1
        found.append((content[start + 1 : end], lines))
        position = end + len(b"</script>")
    return found


def _string_value(node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    return "".join(
        (child.text or b"").decode("utf-8", "replace") for child in node.children if child.type == "string_fragment"
    )


def _imports(root: Node) -> Iterator[tuple[str, int]]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("import_statement", "export_statement"):
            specifier = _string_value(node.child_by_field_name("source"))
            if specifier:
                yield specifier, node.start_point[0] + 1
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if (
                function is not None
                and arguments is not None
                and (function.type == "import" or (function.type == "identifier" and function.text == b"require"))
            ):
                first = next((child for child in arguments.children if child.type == "string"), None)
                specifier = _string_value(first)
                if specifier:
                    yield specifier, node.start_point[0] + 1
        stack.extend(reversed(node.children))


def _exported_methods(root: Node) -> Iterator[tuple[str, int]]:
    for node in root.children:
        if node.type != "export_statement":
            continue
        for child in node.children:
            names = []
            if child.type == "function_declaration":
                names = [child.child_by_field_name("name")]
            elif child.type == "lexical_declaration":
                names = [declarator.child_by_field_name("name") for declarator in child.children
                         if declarator.type == "variable_declarator"]  # fmt: skip
            for name in names:
                text = (name.text or b"").decode() if name is not None else ""
                if text in METHODS:
                    yield text, node.start_point[0] + 1
