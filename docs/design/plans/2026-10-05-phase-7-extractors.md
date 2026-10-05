# Phase 7: More extractors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hamesh's OpenAPI contract, Terraform infrastructure and Swift packages become facts, so the guide, the diagrams and the "you're behind" pages cover the API contract, the cloud resources and the iOS app's structure.

**Architecture:** Three extractors behind the existing interface. New kinds: entities `route`, `schema`, `terraform_module`, `resource`, `swift_target` (Swift packages are `project` facts); relations `uses_schema`, `references`. The imports diagram also draws Swift targets and their dependencies; a new `resources` diagram draws Terraform modules and resources.

**Tech Stack:** `json` and PyYAML for OpenAPI; tree-sitter with tree-sitter-hcl 1.2.0 and tree-sitter-swift 0.7.4 (decision 9's tree-sitter).

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 4.1, 4.4, 5.2.

## Global Constraints
- Extractors read only the files they are offered; every file is bounded by `extract.max_file_bytes`.
- Ids carry no line numbers; a route is `route:<METHOD> <path>`, a schema `schema:<name>`, a resource `resource:<folder>/<type>.<name>`, a module `terraform_module:<folder>`, a Swift target `swift_target:<package folder>/<name>`, an external Swift package `package:swift/<name>`.
- References to things outside the repository (registry modules, Apple frameworks) are dropped and counted, never guessed.
- The extractor list defaults to all five; a target can name fewer.

## Review Focus
1. A malformed OpenAPI document, `.tf` or `Package.swift` is a warning, never a failed update.
2. `$ref` values pointing outside `#/components/schemas/` (remote or file refs) are ignored, never fetched.
3. A Terraform module `source` with `..` resolves only to a folder inside the repository that holds `.tf` files.
4. Deeply nested OpenAPI documents don't recurse without bound.
5. Swift `import` of system frameworks is not counted as unresolved noise per file (counted once per name).

## Tasks
1. Kinds, config (`[openapi] paths`), the openapi extractor; tests.
2. The terraform extractor (modules, resources, module calls, references); tests.
3. The swift extractor (packages, targets, dependencies, external packages, imports per target); tests.
4. Diagrams (Swift targets in the imports diagram; a resources diagram; validation and the page syntax); facts summary; tests.
5. Hamesh: update, check the facts and the diagrams in the browser, Hamesh unchanged; documents; review.
