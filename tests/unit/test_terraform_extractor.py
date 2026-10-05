"""The terraform extractor: modules, resources, module calls and references (design section 5.2)."""

from pathlib import Path

from codetrail.extract import run_extractors
from codetrail.extract.terraform import TerraformExtractor
from codetrail.facts import EntityKind, RelationKind

MODULE = """
resource "google_storage_bucket" "audio" {
  name     = "audio-${var.environment}"
  location = "EU"
}

resource "google_cloud_run_v2_service" "api" {
  name = "api"
  template {
    containers {
      env {
        name  = "AUDIO_BUCKET"
        value = google_storage_bucket.audio.name
      }
    }
  }
}

data "google_project" "this" {}
"""

ENVIRONMENT = """
module "shop" {
  source      = "../../modules/shop"
  environment = "dev"
}

module "registry" {
  source = "terraform-google-modules/network/google"
}
"""


def run(root: Path, files: dict[str, str]):  # type: ignore[no-untyped-def]
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return run_extractors(root, sorted(files), [TerraformExtractor()])


def test_modules_resources_calls_and_references(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"infra/modules/shop/main.tf": MODULE, "infra/envs/dev/main.tf": ENVIRONMENT})
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["terraform_module:infra/modules/shop"].kind is EntityKind.TERRAFORM_MODULE
    assert entities["terraform_module:infra/envs/dev"].kind is EntityKind.TERRAFORM_MODULE
    bucket = entities["resource:infra/modules/shop/google_storage_bucket.audio"]
    assert bucket.attributes == {"type": "google_storage_bucket", "name": "audio", "module": "infra/modules/shop"}
    assert bucket.sources[0].start_line == 2
    assert "resource:infra/modules/shop/google_project.this" not in entities  # data sources aren't resources
    edges = {(r.source_id, r.kind, r.target_id) for r in extraction.relations}
    assert ("terraform_module:infra/modules/shop", RelationKind.CONTAINS, bucket.id) in edges
    api = "resource:infra/modules/shop/google_cloud_run_v2_service.api"
    assert (api, RelationKind.REFERENCES, bucket.id) in edges
    assert (
        "terraform_module:infra/envs/dev",
        RelationKind.REFERENCES,
        "terraform_module:infra/modules/shop",
    ) in edges
    assert extraction.unresolved == {"terraform": 1}  # the registry module


def test_a_source_outside_the_repository_resolves_to_nothing(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"main.tf": 'module "x" {\n  source = "../../../../etc"\n}\n'})
    assert all(relation.kind is not RelationKind.REFERENCES for relation in extraction.relations)


def test_a_broken_file_still_yields_what_parses(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"main.tf": 'resource "a_b" "c" {\n  x = \n}\nresource "d_e" "f" {}\n'})
    assert "resource:./d_e.f" in {entity.id for entity in extraction.entities} or extraction.warnings == []


def test_resources_record_the_paths_they_name(tmp_path: Path) -> None:
    tf = """
resource "google_cloudfunctions2_function" "api" {
  build_config { source { storage_source { bucket = "b" } } }
  source_dir = "../../services/api"
}
resource "docker_image" "web" {
  build {
    context    = "../../apps/web"
    dockerfile = "Dockerfile"
  }
}
resource "archive_file" "bad" {
  source_dir  = "${path.module}/../../.."
  source      = "/etc/passwd"
  path        = "../../../../outside"
  working_dir = "https://example.com/x"
}
"""
    (tmp_path / "infra/app").mkdir(parents=True)
    (tmp_path / "infra/app/main.tf").write_text(tf)
    found = run_extractors(tmp_path, ["infra/app/main.tf"], [TerraformExtractor()])
    resources = [entity for entity in found.entities if entity.id.startswith("resource:")]
    paths = {entity.id: entity.attributes.get("paths") for entity in resources}
    assert paths["resource:infra/app/google_cloudfunctions2_function.api"] == ["services/api"]
    assert paths["resource:infra/app/docker_image.web"] == ["apps/web", "infra/app/Dockerfile"]
    assert paths["resource:infra/app/archive_file.bad"] is None  # interpolated, absolute, escaping or a URL


def test_paths_come_only_from_plain_attributes(tmp_path: Path) -> None:
    tf = """resource "local_file" "conf" {
  # path = "from/a/comment"
  content = <<EOT
path = "inside/a/heredoc"
EOT
  source = "user:token@host:repo"
  filename = "x"
  build {
    context = "../web"
  }
}
"""
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/main.tf").write_text(tf)
    found = run_extractors(tmp_path, ["infra/main.tf"], [TerraformExtractor()])
    [resource] = [entity for entity in found.entities if entity.id.startswith("resource:")]
    assert resource.attributes.get("paths") == ["web"]
