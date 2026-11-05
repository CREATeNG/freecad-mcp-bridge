"""Retrieve the merged PR's Desktop-tested bundle for release verification."""

import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path


BUNDLE_NAME = "freecad-mcp-bridge.mcpb"
WORKFLOW_PATH = ".github/workflows/acceptance-pr.yml"
_CHECKER_PREFIX = "Ricky Boy (AI agent hub name:"
_TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _get(repository: str, path: str) -> bytes:
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(
        f"{api_url}/repos/{repository}/{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "freecad-mcp-bridge-release",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    # Artifact downloads redirect to storage outside the GitHub API.
    request.add_unredirected_header(
        "Authorization", f"Bearer {os.environ['GH_TOKEN']}"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _json(repository: str, path: str):
    return json.loads(_get(repository, path))


def _pages(repository: str, path: str, field: str | None = None):
    page = 1
    while True:
        data = _json(repository, f"{path}?per_page=100&page={page}")
        items = data[field] if field else data
        yield from items
        if len(items) < 100:
            return
        page += 1


def _proof(comments: list[dict], repository: str, head_sha: str) -> dict:
    reports = [
        comment for comment in comments
        if comment.get("author_association") in _TRUSTED_ASSOCIATIONS
        and comment.get("body", "").startswith(_CHECKER_PREFIX)
        and "Claude Desktop GUI test:" in comment["body"]
    ]
    if not reports:
        raise ValueError("The PR has no trusted Desktop check report")
    latest = max(
        reports,
        key=lambda c: (c["created_at"], c["id"]),
    )
    body = latest["body"]
    result = re.search(r"\*\*Claude Desktop GUI test: (\w+)\*\*", body)
    if result is None or result[1] != "PASS":
        raise ValueError("The latest Desktop check report must be PASS")
    run_prefix = rf"https://github\.com/{re.escape(repository)}"
    patterns = {
        "head_sha": r"\bhead\s+`?([0-9a-f]{40})\b",
        "run_id": rf"{run_prefix}/actions/runs/(\d+)",
        "artifact_digest": r"Artifact digest\s+`?(sha256:[0-9a-f]{64})",
        "bundle_sha256": r"\.mcpb inside is\s+`?sha256:([0-9a-f]{64})",
    }
    proof = {"comment_id": latest["id"]}
    for name, pattern in patterns.items():
        match = re.search(pattern, body, re.IGNORECASE)
        if match is None:
            raise ValueError(f"Desktop check report is missing {name}")
        proof[name] = match[1].lower()
    if proof["head_sha"] != head_sha:
        raise ValueError("The latest Desktop check is for a different PR head")
    proof["run_id"] = int(proof["run_id"])
    return proof


def _bundle_bytes(archive: bytes, proof: dict, version: str) -> bytes:
    digest = f"sha256:{hashlib.sha256(archive).hexdigest()}"
    if digest != proof["artifact_digest"]:
        raise ValueError("Downloaded artifact differs from the Desktop check")
    with zipfile.ZipFile(io.BytesIO(archive)) as wrapper:
        bundle = wrapper.read(BUNDLE_NAME)
    if hashlib.sha256(bundle).hexdigest() != proof["bundle_sha256"]:
        raise ValueError("Bundle SHA-256 differs from the Desktop check")
    with zipfile.ZipFile(io.BytesIO(bundle)) as contents:
        manifest = json.loads(contents.read("manifest.json"))
    if manifest.get("version") != version:
        raise ValueError("Desktop-tested bundle has a different version")
    return bundle


def retrieve_bundle(repository: str, release_sha: str, version: str) -> dict:
    """Return the tested bundle bytes and their PR/run/artifact provenance."""
    pulls = list(_pages(repository, f"commits/{release_sha}/pulls"))
    merged = [
        pr for pr in pulls
        if pr.get("merged_at") and pr["merge_commit_sha"] == release_sha
        and pr["base"]["ref"] == "main"
        and (pr["head"].get("repo") or {}).get("full_name") == repository
    ]
    if len(merged) != 1:
        raise ValueError("Release commit must identify one merged main PR")
    pr = merged[0]
    head_sha = pr["head"]["sha"]
    release = _json(repository, f"git/commits/{release_sha}")
    head = _json(repository, f"git/commits/{head_sha}")
    if release["tree"]["sha"] != head["tree"]["sha"]:
        raise ValueError("Merged source differs from the tested PR source")
    comments = list(_pages(repository, f"issues/{pr['number']}/comments"))
    proof = _proof(comments, repository, head_sha)
    run = _json(repository, f"actions/runs/{proof['run_id']}")
    if (
        run["head_sha"] != head_sha
        or run["event"] != "pull_request"
        or run["path"] != WORKFLOW_PATH
        or run["conclusion"] != "success"
    ):
        raise ValueError("Desktop check must name a successful PR bundle run")
    artifacts = list(_pages(
        repository, f"actions/runs/{run['id']}/artifacts", "artifacts"
    ))
    matching = [
        artifact for artifact in artifacts
        if artifact["name"] == BUNDLE_NAME and not artifact["expired"]
        and artifact.get("digest") == proof["artifact_digest"]
    ]
    if len(matching) != 1:
        raise ValueError(
            "Desktop-tested artifact is unavailable or expired. "
            "Run the PR bundle and Desktop checks again before retrying."
        )
    artifact = matching[0]
    archive = _get(repository, f"actions/artifacts/{artifact['id']}/zip")
    bundle = _bundle_bytes(archive, proof, version)
    return {
        "bytes": bundle,
        "pr_number": pr["number"],
        "run_id": run["id"],
        "artifact_id": artifact["id"],
        "comment_id": proof["comment_id"],
        "bundle_sha256": proof["bundle_sha256"],
    }


def main() -> int:
    try:
        result = retrieve_bundle(
            os.environ["GITHUB_REPOSITORY"],
            os.environ["RELEASE_SHA"],
            os.environ["RELEASE_VERSION"],
        )
        Path(BUNDLE_NAME).write_bytes(result.pop("bytes"))
        outputs = "".join(f"{key}={value}\n" for key, value in result.items())
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(outputs)
        print(f"Retrieved Desktop-tested bundle\n{outputs}", end="")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        print(
            f"Cannot retrieve Desktop-tested bundle: {error}", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
