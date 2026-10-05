"""The github_actions extractor records deploy steps, and never step text, env values or secrets (design 17.2)."""

import json
from pathlib import Path

from codetrail.extract import run_extractors
from codetrail.extract.github_actions import GitHubActionsExtractor
from codetrail.facts import EntityKind

WORKFLOW = """name: deploy
on: push
env:
  GLOBAL_TOKEN: ${{ secrets.GLOBAL_TOKEN }}
jobs:
  site:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/site
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - name: Deploy the site
        run: npx wrangler deploy --env production
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
  api:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy the API
        working-directory: ./services/api
        run: |
          gcloud run deploy shop-api --image "$IMAGE" --region europe-west1
      - run: gcloud run deploy ${{ secrets.SERVICE }} --source .
        working-directory: services/other
      - uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: shop-worker
          credentials: ${{ secrets.GCP_KEY }}
      - run: terraform apply -auto-approve
        working-directory: infra/envs/prod
      - run: echo "not a deploy"
  pages:
    steps:
      - uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CF_TOKEN }}
      - run: pnpm deploy
        working-directory: ${{ matrix.folder }}
      - run: flyctl deploy --remote-only
        working-directory: ../outside
"""


def run(root: Path, files: dict[str, str]):  # type: ignore[no-untyped-def]
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return run_extractors(root, sorted(files), [GitHubActionsExtractor()])


def test_deploy_steps_become_deployments(tmp_path: Path) -> None:
    found = run(tmp_path, {".github/workflows/deploy.yml": WORKFLOW, ".github/workflows/notes.md": "x"})
    deployments = {(e.attributes["kind"], e.attributes.get("folder"), e.attributes.get("target"))
                   for e in found.entities if e.kind is EntityKind.DEPLOYMENT}  # fmt: skip
    assert deployments == {
        ("cloudflare", "apps/site", None),
        ("google_cloud", "services/api", "shop-api"),
        ("google_cloud", "services/other", None),  # a templated target is never kept
        ("google_cloud", "", "shop-worker"),
        ("terraform", "infra/envs/prod", None),
        ("cloudflare", "", None),
        ("npm_script", None, None),  # a templated folder is no folder
        ("fly", None, None),  # nor is one that climbs out of the repository
    }


def test_deployments_carry_their_line(tmp_path: Path) -> None:
    found = run(tmp_path, {".github/workflows/deploy.yml": WORKFLOW})
    lines = {e.attributes["kind"]: e.sources[0].start_line for e in found.entities
             if e.attributes.get("folder") == "apps/site"}  # fmt: skip
    assert lines == {"cloudflare": 15}


def test_no_step_text_env_or_secret_reaches_a_fact(tmp_path: Path) -> None:
    found = run(tmp_path, {".github/workflows/deploy.yml": WORKFLOW})
    facts = json.dumps([[e.id, dict(e.attributes)] for e in found.entities])
    for text in ("secrets", "TOKEN", "GCP_KEY", "IMAGE", "europe-west1", "--env", "auto-approve", "matrix"):
        assert text not in facts


def test_a_workflow_that_isnt_a_mapping_adds_nothing(tmp_path: Path) -> None:
    found = run(tmp_path, {".github/workflows/odd.yml": "- just\n- a list\n", ".github/workflows/bad.yaml": "a: [\n"})
    assert found.entities == []
