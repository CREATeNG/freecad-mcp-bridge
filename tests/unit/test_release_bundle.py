"""The released bundle is the one recorded by the PR's Desktop check."""

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import release_bundle


REPOSITORY = "CREATeNG/freecad-mcp-bridge"
HEAD = "a" * 40
MERGE = "b" * 40
TREE = "c" * 40
VERSION = "1.2.3"


def _zip(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def _comment(bundle, archive):
    bundle_hash = hashlib.sha256(bundle).hexdigest()
    artifact_hash = hashlib.sha256(archive).hexdigest()
    run_url = f"https://github.com/{REPOSITORY}/actions/runs/42"
    body = f"""Ricky Boy (AI agent hub name: ricky-example)

**Claude Desktop GUI test: PASS** for version {VERSION}
at head `{HEAD}`.

- Bundle from acceptance-pr.yml run [42]({run_url}).
  Artifact digest `sha256:{artifact_hash}`.
  The .mcpb inside is `sha256:{bundle_hash}`.
"""
    return {
        "id": 99,
        "created_at": "2026-11-05T01:00:00Z",
        "updated_at": "2026-11-05T01:00:00Z",
        "author_association": "MEMBER",
        "body": body,
    }


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.bundle = _zip({
            "manifest.json": json.dumps({"version": VERSION}),
            "index.js": "console.log('tested bytes');",
        })
        self.archive = _zip({release_bundle.BUNDLE_NAME: self.bundle})
        self.comment = _comment(self.bundle, self.archive)
        self.artifact = {
            "id": 123,
            "name": release_bundle.BUNDLE_NAME,
            "expired": False,
            "digest": f"sha256:{hashlib.sha256(self.archive).hexdigest()}",
        }
        self.run = {
            "id": 42,
            "head_sha": HEAD,
            "event": "pull_request",
            "path": release_bundle.WORKFLOW_PATH,
            "conclusion": "success",
        }
        self.data = {
            f"commits/{MERGE}/pulls?per_page=100&page=1": [{
                "number": 7,
                "merged_at": "2026-11-05T02:00:00Z",
                "merge_commit_sha": MERGE,
                "base": {"ref": "main"},
                "head": {
                    "sha": HEAD,
                    "repo": {"full_name": REPOSITORY},
                },
            }],
            f"git/commits/{MERGE}": {"tree": {"sha": TREE}},
            f"git/commits/{HEAD}": {"tree": {"sha": TREE}},
            "issues/7/comments?per_page=100&page=1": [self.comment],
            "actions/runs/42": self.run,
            "actions/runs/42/artifacts?per_page=100&page=1": {
                "artifacts": [self.artifact],
            },
        }

    def _get(self, repository, path):
        self.assertEqual(repository, REPOSITORY)
        if path == "actions/artifacts/123/zip":
            return self.archive
        return json.dumps(self.data[path]).encode()

    def _retrieve(self):
        with patch.object(release_bundle, "_get", side_effect=self._get):
            return release_bundle.retrieve_bundle(REPOSITORY, MERGE, VERSION)

    def test_the_download_keeps_the_exact_desktop_tested_bundle_bytes(self):
        result = self._retrieve()
        self.assertEqual(result["bytes"], self.bundle)
        self.assertEqual(result["artifact_id"], 123)
        self.assertEqual(result["run_id"], 42)
        self.assertEqual(result["comment_id"], 99)
        self.assertEqual(
            result["bundle_sha256"], hashlib.sha256(self.bundle).hexdigest()
        )

    def test_a_different_merged_tree_is_refused(self):
        self.data[f"git/commits/{MERGE}"]["tree"]["sha"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "Merged source differs"):
            self._retrieve()

    def test_a_later_failed_desktop_check_overrides_an_earlier_pass(self):
        failed = copy.deepcopy(self.comment)
        failed.update(id=100, updated_at="2026-11-05T01:30:00Z")
        failed["body"] = failed["body"].replace("test: PASS", "test: FAIL")
        self.data["issues/7/comments?per_page=100&page=1"].append(failed)
        with self.assertRaisesRegex(ValueError, "latest.*must be PASS"):
            self._retrieve()

    def test_an_edited_older_pass_cannot_override_a_later_failure(self):
        failed = copy.deepcopy(self.comment)
        failed.update(
            id=100,
            created_at="2026-11-05T01:30:00Z",
            updated_at="2026-11-05T01:30:00Z",
        )
        failed["body"] = failed["body"].replace("test: PASS", "test: FAIL")
        self.comment["updated_at"] = "2026-11-05T02:00:00Z"
        self.data["issues/7/comments?per_page=100&page=1"].append(failed)
        with self.assertRaisesRegex(ValueError, "latest.*must be PASS"):
            self._retrieve()

    def test_editing_an_old_failure_keeps_the_newer_pass_in_effect(self):
        newer = copy.deepcopy(self.comment)
        newer.update(
            id=100,
            created_at="2026-11-05T01:30:00Z",
            updated_at="2026-11-05T01:30:00Z",
        )
        self.comment["body"] = self.comment["body"].replace(
            "test: PASS", "test: FAIL"
        )
        self.comment["updated_at"] = "2026-11-05T02:00:00Z"
        self.data["issues/7/comments?per_page=100&page=1"].append(newer)
        self.assertEqual(self._retrieve()["comment_id"], 100)

    def test_a_pass_for_an_older_pr_head_is_refused(self):
        self.comment["body"] = self.comment["body"].replace(HEAD, "d" * 40)
        with self.assertRaisesRegex(ValueError, "different PR head"):
            self._retrieve()

    def test_an_untrusted_comment_cannot_authorize_a_bundle(self):
        self.comment["author_association"] = "NONE"
        with self.assertRaisesRegex(ValueError, "no trusted Desktop"):
            self._retrieve()

    def test_the_named_run_must_be_successful_pr_acceptance_at_that_head(self):
        original = copy.deepcopy(self.run)
        for field, value in (
            ("head_sha", "d" * 40),
            ("event", "push"),
            ("path", ".github/workflows/tests.yml"),
            ("conclusion", "failure"),
        ):
            with self.subTest(field=field):
                self.run.update(original)
                self.run[field] = value
                with self.assertRaisesRegex(ValueError, "successful PR"):
                    self._retrieve()

    def test_a_rerun_cannot_substitute_another_artifact_at_the_same_head(self):
        self.artifact["digest"] = "sha256:" + "d" * 64
        with self.assertRaisesRegex(ValueError, "unavailable or expired"):
            self._retrieve()

    def test_an_expired_artifact_stops_the_release(self):
        self.artifact["expired"] = True
        with self.assertRaisesRegex(ValueError, "unavailable or expired"):
            self._retrieve()

    def test_a_corrupted_artifact_download_is_refused(self):
        self.archive += b"changed in transit"
        with self.assertRaisesRegex(ValueError, "Downloaded artifact differs"):
            self._retrieve()

    def test_the_inner_bundle_must_match_the_desktop_hash(self):
        altered = _zip({release_bundle.BUNDLE_NAME: b"untested bundle"})
        proof = {
            "artifact_digest": f"sha256:{hashlib.sha256(altered).hexdigest()}",
            "bundle_sha256": hashlib.sha256(self.bundle).hexdigest(),
        }
        with self.assertRaisesRegex(ValueError, "Bundle SHA-256 differs"):
            release_bundle._bundle_bytes(altered, proof, VERSION)

    def test_the_bundle_manifest_must_name_the_release_version(self):
        with self.assertRaisesRegex(ValueError, "different version"):
            with patch.object(release_bundle, "_get", side_effect=self._get):
                release_bundle.retrieve_bundle(REPOSITORY, MERGE, "9.9.9")

    def test_a_desktop_report_on_a_later_comment_page_is_found(self):
        path = "issues/7/comments?per_page=100&page="
        self.data[path + "1"] = [{} for _ in range(100)]
        self.data[path + "2"] = [self.comment]
        self.assertEqual(self._retrieve()["bytes"], self.bundle)

    def test_the_workflow_entry_point_writes_verified_bytes_and_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "outputs"
            env = {
                "GITHUB_REPOSITORY": REPOSITORY,
                "RELEASE_SHA": MERGE,
                "RELEASE_VERSION": VERSION,
                "GITHUB_OUTPUT": str(output),
            }
            with (
                contextlib.chdir(temp),
                contextlib.redirect_stdout(io.StringIO()),
                patch.dict(os.environ, env),
                patch.object(release_bundle, "_get", side_effect=self._get),
            ):
                self.assertEqual(release_bundle.main(), 0)
            bundle = Path(temp) / release_bundle.BUNDLE_NAME
            self.assertEqual(bundle.read_bytes(), self.bundle)
            self.assertIn(
                hashlib.sha256(self.bundle).hexdigest(), output.read_text()
            )

    def test_a_rejected_bundle_leaves_no_release_file_or_outputs(self):
        self.archive += b"corrupted"
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "outputs"
            env = {
                "GITHUB_REPOSITORY": REPOSITORY,
                "RELEASE_SHA": MERGE,
                "RELEASE_VERSION": VERSION,
                "GITHUB_OUTPUT": str(output),
            }
            with (
                contextlib.chdir(temp),
                contextlib.redirect_stderr(io.StringIO()),
                patch.dict(os.environ, env),
                patch.object(release_bundle, "_get", side_effect=self._get),
            ):
                self.assertEqual(release_bundle.main(), 1)
            bundle = Path(temp) / release_bundle.BUNDLE_NAME
            self.assertFalse(bundle.exists())
            self.assertFalse(output.exists())

    def test_artifact_redirects_drop_the_github_authorization_header(self):
        with (
            patch.dict(os.environ, {"GH_TOKEN": "test-token"}),
            patch.object(urllib.request, "urlopen") as open_url,
        ):
            response = open_url.return_value.__enter__.return_value
            response.read.return_value = b"artifact"
            self.assertEqual(
                release_bundle._get(REPOSITORY, "actions/artifacts/123/zip"),
                b"artifact",
            )
        request = open_url.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"), "Bearer test-token"
        )
        redirect = urllib.request.HTTPRedirectHandler().redirect_request(
            request, None, 302, "", {}, "https://storage.example/artifact.zip"
        )
        self.assertFalse(redirect.has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
