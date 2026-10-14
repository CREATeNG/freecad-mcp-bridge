"""Requirements on reading a job's output page by page.

Each test name, and the first line of its docstring, states one requirement
as an agent reading output through get_output_page experiences it, against a
real bridge in a real FreeCAD. Tests that need a short response timeout, a
small page size or no history retention set the bridge's own preference
through execute_python, and restore it afterwards.
"""

import math
import time
import unittest

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


def stored_pages(results):
    return [result for result in results if "page_no" in result]


def chars(result):
    return sum(len(chunk["text"]) for chunk in result["page"])


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
        """Output larger than the page size cap is split across pages of no more than the cap's characters, with no text lost or duplicated."""
        with mcp.preference("MaxPageSize", SMALL_PAGE_KB):
            results = mcp.follow(mcp.execute(LONG_JOB))
        pages = stored_pages(results)
        self.assertGreaterEqual(len(pages), math.ceil(len(LONG_TEXT) / SMALL_PAGE_CHARS))
        for result in results:
            self.assertLessEqual(chars(result), SMALL_PAGE_CHARS)
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


class SharedHistoryTests(unittest.TestCase):
    def test_any_client_holding_a_job_token_can_read_that_jobs_pages(self):
        """Any client holding a job token can read that job's pages, from its own connection."""
        first = mcp.execute("print('shared output')")
        # A second client: its own handshake; every request is its own
        # HTTP connection, sharing nothing with the first client but the token.
        self.assertEqual(
            mcp.post(mcp.request("initialize", mcp.INITIALIZE_PARAMS)).status, 200
        )
        again = mcp.get_page(first["job_token"], first["page_no"])
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


class RetentionTests(unittest.TestCase):
    def assertExpired(self, final):
        again = mcp.get_page(final["job_token"], final["page_no"])
        self.assertEqual(again.get("error"), "unknown or expired job_token")

    def assertKept(self, final):
        again = mcp.get_page(final["job_token"], final["page_no"])
        self.assertNotIn("error", again)
        self.assertEqual(again["page"], final["page"])

    def test_the_retention_in_effect_at_the_final_page_applies(self):
        """The retention in effect when the final page returns applies."""
        retention = "MaxHistoryRetentionMinutes"
        with mcp.preference("MaxResponseTimeoutSeconds", 1):
            # Started under the default retention, finished under none.
            first = mcp.execute(SLOW_JOB)
            self.assertTrue(first["has_more"])
            with mcp.preference(retention, NO_RETENTION):
                final = mcp.follow(first)[-1]
                self.assertExpired(final)

            # Started under none, finished under the default retention.
            with mcp.preference(retention, NO_RETENTION):
                first = mcp.execute(SLOW_JOB)
                self.assertTrue(first["has_more"])
            final = mcp.follow(first)[-1]
            self.assertKept(final)


if __name__ == "__main__":
    unittest.main()
