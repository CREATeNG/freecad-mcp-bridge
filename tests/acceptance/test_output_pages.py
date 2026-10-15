"""Requirements on reading a job's output page by page.

Each test name, and the first line of its docstring, states one requirement
as an agent reading output through get_output_page experiences it, against a
real bridge in a real FreeCAD. Tests that need a short response timeout, a
small page size or no history retention set the bridge's own preference
through execute_python, and restore it afterwards.
"""

import math
import os
import tempfile
import time
import unittest
from contextlib import contextmanager

from freecad.mcp_bridge.mcp_protocol import serialized_page_length
from tests.acceptance import mcp_client as mcp

SMALL_PAGE_KB = 1  # the smallest page size cap the preference allows
SMALL_PAGE_CHARS = SMALL_PAGE_KB * 1024
LONG_TEXT = "".join(f"{i:04d}," for i in range(1000))  # 5000 characters
# One write larger than a page, then many small ones.
LONG_JOB = (
    "import sys\n"
    "text = ''.join(f'{i:04d},' for i in range(1000))\n"
    "sys.stdout.write(text[:3000])\n"
    "for at in range(3000, len(text), 100):\n"
    "    sys.stdout.write(text[at:at + 100])\n"
)
# Under a 1 s response timeout and the small page size: quiet at first, so
# the initiating response is empty and unnumbered; then several pages of
# output; then quiet again, so the final page comes empty after a retry.
QUIET_LONG_QUIET_JOB = (
    "import time\ntime.sleep(2)\n" + LONG_JOB + "time.sleep(3)\n"
)
# The documented field sets (design/addon-hosted-mcp-server.md, "Result
# shape"): page_no only on a stored page, job_token while the history
# exists, error only when the tool cannot return output.
STORED_PAGE_FIELDS = {"page", "page_no", "has_more", "job_token"}
EMPTY_RETRY_FIELDS = {"page", "has_more", "job_token"}
ERROR_FIELDS = {"page", "has_more", "error"}


@contextmanager
def quiet_long_quiet_job_settings():
    with mcp.preference("MaxResponseTimeoutSeconds", 1):
        with mcp.preference("MaxPageSize", SMALL_PAGE_KB):
            yield


def stored_pages(results):
    return [result for result in results if "page_no" in result]


def wire_length(result):
    return serialized_page_length(
        result["page"], result["page_no"], result["job_token"]
    )


class SlowJobTests(unittest.TestCase):
    def test_a_job_outlasting_the_response_timeout_returns_early_and_the_rest_is_fetched_later(self):
        """A job that runs longer than the response timeout returns has_more true quickly, and the rest is fetched with get_output_page until has_more is false."""
        with mcp.preference("MaxResponseTimeoutSeconds", 1):
            started = time.monotonic()
            first = mcp.execute(
                "import time\nprint('start')\ntime.sleep(4)\nprint('end')\n"
            )
            elapsed = time.monotonic() - started
            results = mcp.follow(first)
        self.assertTrue(first["has_more"])
        self.assertLess(elapsed, 3)
        self.assertTrue("start\nend\n".startswith(mcp.text([first])))
        self.assertEqual(mcp.text(results), "start\nend\n")
        self.assertFalse(results[-1]["has_more"])


class PageSizeTests(unittest.TestCase):
    def test_output_larger_than_the_page_size_cap_is_split_across_pages_without_loss(self):
        """Output larger than the page size cap is split across pages whose JSON tool result stays within the cap, with no text lost or duplicated."""
        with mcp.preference("MaxPageSize", SMALL_PAGE_KB):
            results = mcp.follow(mcp.execute(LONG_JOB))
        pages = stored_pages(results)
        self.assertGreaterEqual(len(pages), math.ceil(len(LONG_TEXT) / SMALL_PAGE_CHARS))
        for result in pages:
            self.assertLessEqual(wire_length(result), SMALL_PAGE_CHARS)
        self.assertEqual([r["page_no"] for r in pages], list(range(len(pages))))
        self.assertEqual(mcp.text(results), LONG_TEXT)

    def test_fetching_an_earlier_page_again_returns_the_same_page(self):
        """Stored pages replay: fetching an earlier page_no again returns the same content, page_no and has_more."""
        with mcp.preference("MaxPageSize", SMALL_PAGE_KB):
            results = mcp.follow(mcp.execute(LONG_JOB))
        pages = stored_pages(results)
        self.assertGreater(len(pages), 1)
        token = results[0]["job_token"]
        for original in pages:
            with self.subTest(page_no=original["page_no"]):
                again = mcp.get_page(token, original["page_no"])
                for key in ("page", "page_no", "has_more"):
                    self.assertEqual(again[key], original[key])


class OutOfRangeTests(unittest.TestCase):
    def assertOutOfRange(self, result):
        self.assertEqual(result["error"], "page_no out of range")
        self.assertEqual(result["page"], [])
        self.assertFalse(result["has_more"])
        self.assertNotIn("job_token", result)

    def test_a_negative_or_past_the_final_page_no_returns_page_no_out_of_range(self):
        """A negative page_no, or one past a finished job's final page, returns "page_no out of range"."""
        first = mcp.execute("print('one page')")
        self.assertFalse(first["has_more"])
        for page_no in (-1, first["page_no"] + 1, first["page_no"] + 5):
            with self.subTest(page_no=page_no):
                self.assertOutOfRange(mcp.get_page(first["job_token"], page_no))

    def test_skipping_ahead_of_a_running_job_returns_page_no_out_of_range(self):
        """A page_no that skips ahead of the next page of a running job returns "page_no out of range"."""
        with mcp.preference("MaxResponseTimeoutSeconds", 1):
            first = mcp.execute("import time\nprint('a')\ntime.sleep(3)\n")
            self.assertTrue(first["has_more"])
            skipped = first.get("page_no", -1) + 2
            self.assertOutOfRange(mcp.get_page(first["job_token"], skipped))
            mcp.follow(first)

    def test_an_unknown_job_token_returns_unknown_or_expired_job_token(self):
        """An unknown job token returns "unknown or expired job_token" with an empty final result."""
        result = mcp.get_page("0" * 32, 0)
        self.assertEqual(result["error"], "unknown or expired job_token")
        self.assertEqual(result["page"], [])
        self.assertFalse(result["has_more"])


class ResultFieldTests(unittest.TestCase):
    def assertFields(self, result, expected, has_more):
        self.assertEqual(set(result), expected)
        self.assertIs(result["has_more"], has_more)

    def test_each_tool_result_carries_exactly_its_documented_fields(self):
        """Every tool result carries page and has_more; page_no only on a stored page, job_token only while the job's history exists, and error only when the tool cannot return output."""
        with quiet_long_quiet_job_settings():
            results = mcp.follow(mcp.execute(QUIET_LONG_QUIET_JOB))
        retries = [result for result in results if "page_no" not in result]
        pages = stored_pages(results)
        self.assertTrue(retries, "precondition: a quiet unfinished response")
        self.assertGreater(len(pages), 1, "precondition: several pages")
        token = results[0]["job_token"]

        for result in retries:
            with self.subTest(kind="quiet unfinished response"):
                self.assertFields(result, EMPTY_RETRY_FIELDS, has_more=True)
                self.assertEqual(result["page"], [])
        for result in pages[:-1]:
            with self.subTest(kind="stored page", page_no=result["page_no"]):
                self.assertFields(result, STORED_PAGE_FIELDS, has_more=True)
        with self.subTest(kind="final page"):
            self.assertFields(results[-1], STORED_PAGE_FIELDS, has_more=False)
        with self.subTest(kind="final page of an initiating call"):
            finished = mcp.execute("print('done at once')")
            self.assertFields(finished, STORED_PAGE_FIELDS, has_more=False)
        with self.subTest(kind="replayed page"):
            again = mcp.get_page(token, 0)
            self.assertFields(again, STORED_PAGE_FIELDS, has_more=True)

        with tempfile.TemporaryDirectory() as folder:
            unreadable = mcp.execute_file(os.path.join(folder, "missing.py"))
        past_the_end = mcp.get_page(token, pages[-1]["page_no"] + 1)
        unknown = mcp.get_page("0" * 32, 0)
        errors = {
            "unreadable file": unreadable,
            "page_no out of range": past_the_end,
            "unknown job_token": unknown,
        }
        for kind, result in errors.items():
            with self.subTest(kind=f"error: {kind}"):
                self.assertFields(result, ERROR_FIELDS, has_more=False)
                self.assertEqual(result["page"], [])


class PagePromiseTests(unittest.TestCase):
    def test_has_more_true_is_always_followed_by_a_further_page_until_the_final_page(self):
        """While the bridge runs, has_more true precedes at least one more fetchable page: the one numbered past the last, until the job's final page."""
        with quiet_long_quiet_job_settings():
            # follow() also checks this promise on every job it reads.
            results = mcp.follow(mcp.execute(QUIET_LONG_QUIET_JOB))
        # The job's shape: a quiet start, pages of output, a quiet end.
        self.assertNotIn("page_no", results[0], "precondition: quiet start")
        self.assertEqual(results[-1]["page"], [], "precondition: quiet end")

        pages = stored_pages(results)
        self.assertEqual(
            [page["page_no"] for page in pages], list(range(len(pages)))
        )
        last_page_no = None
        for at, result in enumerate(results):
            last_page_no = result.get("page_no", last_page_no)
            if not result["has_more"]:
                continue
            with self.subTest(after=at):
                later = stored_pages(results[at + 1:])
                expected = 0 if last_page_no is None else last_page_no + 1
                self.assertTrue(later, "has_more true, but no further page")
                self.assertEqual(later[0]["page_no"], expected)
        self.assertFalse(results[-1]["has_more"])
        self.assertIn("page_no", results[-1])


class SharedHistoryTests(unittest.TestCase):
    def test_any_client_holding_a_job_token_can_read_that_jobs_pages(self):
        """Any client holding a job token can read that job's pages, from its own connection."""
        first = mcp.execute("print('shared output')")
        # A second client, by its own identity; every request is its own
        # HTTP connection, sharing nothing with the first client but the token.
        message = mcp.request(
            "tools/call",
            {
                "name": "get_output_page",
                "arguments": {
                    "job_token": first["job_token"],
                    "page_no": first["page_no"],
                },
            },
            client_info={"name": "another-client", "version": "0"},
        )
        again = mcp.payload(mcp.tool_result(mcp.post(message)))
        self.assertEqual(again["page"], first["page"])
        self.assertEqual(mcp.text([again]), "shared output\n")

    def test_each_job_has_its_own_token_and_reading_one_never_drains_another(self):
        """Each job has its own token, and fetching one job's output never drains another's."""
        with mcp.preference("MaxResponseTimeoutSeconds", 1):
            x = mcp.execute(
                "import time\nprint('x-first')\ntime.sleep(2)\nprint('x-last')\n"
            )
            y = mcp.execute("print('y-only')")  # queued behind x
            self.assertNotEqual(x["job_token"], y["job_token"])
            # Read y to the end first, while x's later output is still to come.
            y_text = mcp.text(mcp.follow(y))
            x_text = mcp.text(mcp.follow(x))
        self.assertEqual(y_text, "y-only\n")
        self.assertEqual(x_text, "x-first\nx-last\n")


# Zero minutes, below the preference page's minimum of one, so a history
# it applies to expires at once instead of after a minute's wait.
NO_RETENTION = 0
# Outlasts a 1 s response timeout, so its final page comes from a later call.
SLOW_JOB = "import time\nprint('first')\ntime.sleep(3)\nprint('last')\n"
RETENTION = "MaxHistoryRetentionMinutes"


def _set_retention(minutes):
    """Script text that sets retention before the running job returns."""
    return (
        "from freecad.mcp_bridge.constants import PREF_GROUP\n"
        "App.ParamGet(PREF_GROUP).SetInt("
        f"'MaxHistoryRetentionMinutes', {int(minutes)})\n"
    )


def _job_that_sets_retention(minutes, tail):
    """Print, wait out the first page, set retention, then `tail`."""
    return (
        "import time\n"
        "print('first')\n"
        "time.sleep(3)\n"
        + _set_retention(minutes)
        + tail
    )


class RetentionTests(unittest.TestCase):
    def test_the_retention_in_effect_when_execution_finishes_applies(self):
        """The retention in effect when execution finishes applies to that job."""
        with mcp.preference("MaxResponseTimeoutSeconds", 1):
            with mcp.preference(RETENTION, 5):
                # The script sets zero before it returns. Raising the
                # preference after that must not revive the history.
                code = _job_that_sets_retention(
                    NO_RETENTION, "print('last')\n"
                )
                first = mcp.execute(code)
                self.assertTrue(first["has_more"])
                time.sleep(4)
                with mcp.preference(RETENTION, 5):
                    expired = mcp.get_page(first["job_token"], 1)
                self.assertEqual(
                    expired.get("error"), "unknown or expired job_token"
                )
                self.assertFalse(expired["has_more"])
                self.assertEqual(expired["page"], [])

                # Finish under five minutes, then set zero before the
                # final fetch. That later value controls neither the
                # interval sampled at finish nor the extension on a read.
                first = mcp.execute(SLOW_JOB)
                self.assertTrue(first["has_more"])
                time.sleep(4)
                with mcp.preference(RETENTION, NO_RETENTION):
                    final = mcp.get_page(first["job_token"], 1)
                    self.assertNotIn("error", final)
                    self.assertFalse(final["has_more"])
                    replay = mcp.get_page(
                        final["job_token"], final["page_no"]
                    )
                self.assertNotIn("error", replay)
                self.assertEqual(replay["page"], final["page"])

    def test_every_script_exit_notifies_paging_without_a_fetch(self):
        """Success, a raised exception and sys.exit each start retention before the next fetch."""
        tails = (
            "print('last')\n",
            "raise RuntimeError('boom')\n",
            "import sys\nsys.exit(0)\n",
        )
        with mcp.preference("MaxResponseTimeoutSeconds", 1):
            with mcp.preference(RETENTION, 5):
                for tail in tails:
                    code = _job_that_sets_retention(NO_RETENTION, tail)
                    first = mcp.execute(code)
                    self.assertTrue(first["has_more"], tail)
                    time.sleep(4)
                    with mcp.preference(RETENTION, 5):
                        page = mcp.get_page(first["job_token"], 1)
                    self.assertEqual(
                        page.get("error"),
                        "unknown or expired job_token",
                        tail,
                    )
                    self.assertEqual(page["page"], [], tail)


if __name__ == "__main__":
    unittest.main()
