"""Index notifications deliver public metadata and preserve retries."""

import contextlib
import http.client
import io
import json
import os
import unittest
from unittest.mock import patch

from scripts import notify_index_ready


REPOSITORY = "CREATeNG/freecad-mcp-bridge"
SHA = "a" * 40
RUN_URL = f"https://github.com/{REPOSITORY}/actions/runs/123"
URL = "https://receiver.example/hooks/private-path?routine=index-ready"
KEY = "private-sender-key"


class IndexNotificationTests(unittest.TestCase):
    def setUp(self):
        self.event = notify_index_ready.index_ready_event(
            REPOSITORY, "1.2.3", SHA, RUN_URL
        )
        self.env = {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "123",
            "INDEX_VERSION": "1.2.3",
            "INDEX_RELEASE_SHA": SHA,
            "RICKY_INDEX_WEBHOOK_URL": URL,
            "RICKY_INDEX_WEBHOOK_KEY": KEY,
        }

    def test_the_event_identifies_the_confirmed_release_and_workflow(self):
        self.assertEqual(self.event, {
            "event": "freecad.index_ready",
            "repository": REPOSITORY,
            "version": "1.2.3",
            "release_tag": "v1.2.3",
            "release_sha": SHA,
            "workflow_run_url": RUN_URL,
            "delivery_id": f"{REPOSITORY}:index-ready:1.2.3",
        })

    def test_retrying_the_same_version_keeps_its_delivery_id(self):
        retry = notify_index_ready.index_ready_event(
            REPOSITORY, "1.2.3", SHA, RUN_URL + "4"
        )
        self.assertEqual(retry["delivery_id"], self.event["delivery_id"])
        newer = notify_index_ready.index_ready_event(
            REPOSITORY, "1.2.4", SHA, RUN_URL
        )
        self.assertNotEqual(newer["delivery_id"], self.event["delivery_id"])

    def test_the_post_uses_json_and_adds_bearer_to_the_raw_key(self):
        with patch.object(http.client, "HTTPSConnection") as connect:
            connection = connect.return_value
            connection.getresponse.return_value.status = 202
            self.assertEqual(
                notify_index_ready._post(URL, KEY, self.event), 202
            )
        connect.assert_called_once_with("receiver.example", None, timeout=30)
        args = connection.request.call_args
        self.assertEqual(args.args, (
            "POST", "/hooks/private-path?routine=index-ready"
        ))
        self.assertEqual(json.loads(args.kwargs["body"]), self.event)
        self.assertEqual(
            args.kwargs["headers"]["Authorization"], f"Bearer {KEY}"
        )
        self.assertEqual(
            args.kwargs["headers"]["Content-Type"], "application/json"
        )
        self.assertNotIn(KEY, args.kwargs["body"].decode())
        self.assertNotIn(URL, args.kwargs["body"].decode())
        connection.close.assert_called_once()

    def test_http_endpoints_are_refused_before_sending_credentials(self):
        with patch.object(http.client, "HTTPSConnection") as connect:
            with self.assertRaises(ValueError):
                notify_index_ready._post(
                    "http://receiver.example/hooks", KEY, self.event
                )
        connect.assert_not_called()

    def test_a_blank_sender_key_is_refused(self):
        with patch.object(http.client, "HTTPSConnection") as connect:
            with self.assertRaises(ValueError):
                notify_index_ready._post(URL, "", self.event)
        connect.assert_not_called()

    def test_a_connection_error_closes_the_connection(self):
        with patch.object(http.client, "HTTPSConnection") as connect:
            connection = connect.return_value
            connection.request.side_effect = TimeoutError("timed out")
            with self.assertRaises(TimeoutError):
                notify_index_ready._post(URL, KEY, self.event)
        connection.close.assert_called_once()

    def _run(self, status=202, error=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.env),
            patch.object(notify_index_ready, "_post") as post,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            post.return_value = status
            post.side_effect = error
            result = notify_index_ready.main()
        return result, stdout.getvalue(), stderr.getvalue(), post

    def test_a_successful_post_completes_the_notification_step(self):
        result, stdout, stderr, post = self._run()
        self.assertEqual(result, 0)
        self.assertIn("1.2.3", stdout)
        self.assertEqual(stderr, "")
        post.assert_called_once_with(URL, KEY, self.event)

    def test_a_non_success_status_keeps_the_notification_step_failed(self):
        for status in [301, 401, 429, 500]:
            with self.subTest(status=status):
                result, stdout, stderr, _ = self._run(status=status)
                self.assertEqual(result, 1)
                self.assertEqual(stdout, "")
                self.assertIn(str(status), stderr)

    def test_a_transport_error_does_not_print_credentials(self):
        result, stdout, stderr, _ = self._run(
            error=OSError(f"failed at {URL} with {KEY}")
        )
        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertNotIn(URL, stderr)
        self.assertNotIn(KEY, stderr)

    def test_missing_configuration_fails_before_the_request(self):
        del self.env["RICKY_INDEX_WEBHOOK_KEY"]
        with patch.dict(os.environ, {}, clear=True):
            result, _, _, post = self._run()
        self.assertEqual(result, 1)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
