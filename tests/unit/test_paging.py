"""Requirements on a job's paged output and its page history (paging.py).

Each test name, and the first line of its docstring, states one requirement
as an agent reading output through get_output_page experiences it. The job's
output queue is fed directly, standing in for the executor.
"""

import time
import unittest
from unittest import mock

from freecad.mcp_bridge import paging

PAGE_SIZE = 10  # characters
SHORT_WAIT_MS = 50
LONG_WAIT_MS = 5000
RETENTION_MS = 60_000


def new_job(*chunks, finished=False):
    """Start a job whose output queue already holds `chunks` (and its end)."""
    token, output = paging.start_page()
    for chunk in chunks:
        output.put(chunk)
    if finished:
        output.put(paging.SENTINEL)
    return token, output


def fetch(token, page_no, wait_ms=SHORT_WAIT_MS, page_size=PAGE_SIZE):
    return paging.fetch(token, page_no, wait_ms, page_size, RETENTION_MS)


def read_all(token, wait_ms=SHORT_WAIT_MS, page_size=PAGE_SIZE):
    """Follow has_more from page 0 to the final page; return the stored pages."""
    pages, page_no = [], 0
    while True:
        result = fetch(token, page_no, wait_ms, page_size)
        if "page_no" in result:
            pages.append(result)
            page_no += 1
        if not result["has_more"]:
            return pages


def chars(page):
    return sum(len(chunk["text"]) for chunk in page)


class FakeClock:
    """Stands in for paging's monotonic clock, so retention needs no sleeping."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


class PageBoundsTests(unittest.TestCase):
    def setUp(self):
        paging.clear_all()

    def test_a_page_closes_when_the_output_wait_limit_expires(self):
        """A page closes when the output-wait limit expires, keeping what arrived."""
        token, _ = new_job(("stdout", "hello"))
        started = time.monotonic()
        result = fetch(token, 0, wait_ms=200)
        elapsed = time.monotonic() - started
        self.assertEqual(result["page"], [{"stream": "stdout", "text": "hello"}])
        self.assertTrue(result["has_more"])
        self.assertGreaterEqual(elapsed, 0.15)
        self.assertLess(elapsed, 2.0)

    def test_a_page_closes_as_soon_as_it_is_full(self):
        """A page closes as soon as it is full, without waiting out the limit."""
        token, _ = new_job(("stdout", "a" * 6), ("stdout", "b" * 6))
        started = time.monotonic()
        result = fetch(token, 0, wait_ms=LONG_WAIT_MS)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(chars(result["page"]), PAGE_SIZE)
        self.assertTrue(result["has_more"])

    def test_a_page_closes_as_soon_as_the_job_output_is_complete(self):
        """A page closes as soon as the job's output is complete, without waiting out the limit."""
        token, _ = new_job(("stdout", "done\n"), finished=True)
        started = time.monotonic()
        result = fetch(token, 0, wait_ms=LONG_WAIT_MS)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(result["page"], [{"stream": "stdout", "text": "done\n"}])
        self.assertFalse(result["has_more"])

    def test_a_finished_job_whose_output_fits_one_page_returns_it_all_at_once(self):
        """A job that finishes with output fitting one page returns it with has_more false in one response."""
        chunks = [("stdout", "abc"), ("stderr", "de"), ("stdout", "fgh")]
        token, _ = new_job(*chunks, finished=True)
        result = fetch(token, 0)
        self.assertEqual(
            result["page"], [{"stream": s, "text": t} for s, t in chunks]
        )
        self.assertEqual(result["page_no"], 0)
        self.assertFalse(result["has_more"])

    def test_a_page_never_holds_more_text_than_the_page_size_cap(self):
        """A page holds no more than the configured number of text characters, independent of JSON size."""
        # Quotes, backslashes, newlines and non-ASCII all grow when encoded as
        # JSON; the cap counts characters of text, not encoded bytes.
        token, _ = new_job(("stdout", '"\\\né' * 10), finished=True)
        pages = read_all(token)
        for result in pages:
            self.assertLessEqual(chars(result["page"]), PAGE_SIZE)
        self.assertEqual(chars(pages[0]["page"]), PAGE_SIZE)

    def test_an_oversized_chunk_is_split_across_pages_intact(self):
        """An oversized output chunk is split across pages without losing or duplicating text or changing its stream label."""
        long_out = "".join(str(i % 10) for i in range(25))
        chunks = [("stdout", long_out), ("stderr", "err-1234567")]
        token, _ = new_job(*chunks, finished=True)
        pieces = [c for result in read_all(token) for c in result["page"]]
        self.assertGreater(len(pieces), len(chunks))  # it really was split
        rejoined = []
        for piece in pieces:
            if rejoined and rejoined[-1][0] == piece["stream"]:
                rejoined[-1] = (piece["stream"], rejoined[-1][1] + piece["text"])
            else:
                rejoined.append((piece["stream"], piece["text"]))
        self.assertEqual(rejoined, chunks)


class PageHistoryTests(unittest.TestCase):
    def setUp(self):
        paging.clear_all()

    def test_stored_pages_are_numbered_consecutively_from_zero(self):
        """Stored pages are numbered from zero consecutively; only pages with content, and the final page, get numbers."""
        token, output = new_job()
        numbers = []

        def poll(page_no):
            result = fetch(token, page_no)
            if "page_no" in result:
                numbers.append(result["page_no"])
            return result

        poll(0)  # nothing yet: no number
        output.put(("stdout", "a"))
        poll(0)
        poll(1)  # nothing yet: no number
        output.put(("stdout", "b"))
        poll(1)
        output.put(paging.SENTINEL)
        final = poll(2)  # no text, but final: numbered
        self.assertEqual(numbers, [0, 1, 2])
        self.assertEqual(final["page"], [])
        self.assertFalse(final["has_more"])

    def test_an_empty_unfinished_drain_is_unnumbered_and_can_be_retried(self):
        """An empty, unfinished drain returns page [] with has_more true, the token, and no page_no; retrying the same page_no later works."""
        token, output = new_job()
        empty = fetch(token, 0)
        self.assertEqual(empty, {"page": [], "has_more": True, "job_token": token})
        output.put(("stdout", "late"))
        retry = fetch(token, 0)
        self.assertEqual(retry["page_no"], 0)
        self.assertEqual(retry["page"], [{"stream": "stdout", "text": "late"}])

    def test_a_job_with_no_final_text_still_ends_with_a_numbered_empty_page(self):
        """A job with no final text still ends with a numbered empty final page, has_more false."""
        token, output = new_job(("stdout", "x"))
        self.assertTrue(fetch(token, 0)["has_more"])
        output.put(paging.SENTINEL)
        final = fetch(token, 1)
        self.assertEqual(final["page"], [])
        self.assertEqual(final["page_no"], 1)
        self.assertFalse(final["has_more"])
        self.assertEqual(final["job_token"], token)

    def test_the_end_of_output_marker_never_appears_in_a_page(self):
        """The internal end-of-output marker never appears in a page."""
        jobs = [
            new_job(finished=True),  # no output at all
            new_job(("stdout", "abc"), finished=True),  # end after a part page
            new_job(("stdout", "a" * PAGE_SIZE), finished=True),  # after a full page
            new_job(("stdout", "a" * 25), finished=True),  # after a split chunk
        ]
        for token, _ in jobs:
            for result in read_all(token):
                for chunk in result["page"]:
                    self.assertEqual(set(chunk), {"stream", "text"})
                    self.assertIsInstance(chunk["text"], str)

    def test_fetching_a_stored_page_again_replays_it_exactly(self):
        """Fetching a stored page again replays it exactly (page, page_no, original has_more) and does not consume new output."""
        token, output = new_job(("stdout", "first"))
        original = fetch(token, 0)
        output.put(("stdout", "second"))
        output.put(paging.SENTINEL)
        self.assertEqual(fetch(token, 0), original)
        following = fetch(token, 1)
        self.assertEqual(following["page"], [{"stream": "stdout", "text": "second"}])
        self.assertFalse(following["has_more"])
        self.assertEqual(fetch(token, 0), original)  # still has_more true

    def test_fetching_the_next_page_number_drains_forward(self):
        """Fetching page_no = max+1 drains new output forward into the next page."""
        token, output = new_job(("stdout", "one"))
        fetch(token, 0)
        output.put(("stderr", "two"))
        result = fetch(token, 1)
        self.assertEqual(result["page_no"], 1)
        self.assertEqual(result["page"], [{"stream": "stderr", "text": "two"}])
        self.assertTrue(result["has_more"])

    # Known divergence: paging.fetch's docstring promises an out-of-range
    # error, but a negative page_no indexes the history from the end (or
    # raises IndexError on an empty history). The HTTP layer
    # (http_server._as_page_no) rejects negatives before reaching paging,
    # so an agent does not see this; the module's own contract does.
    def test_a_negative_page_no_is_out_of_range(self):
        """A negative page_no returns the error "page_no out of range"."""
        token, _ = new_job(("stdout", "x"))
        fetch(token, 0)
        self.assertEqual(
            fetch(token, -1),
            {"page": [], "has_more": False, "error": "page_no out of range"},
        )

    def test_a_page_no_beyond_the_next_one_is_out_of_range(self):
        """A page_no that skips ahead of the next page returns the error "page_no out of range"."""
        token, _ = new_job(("stdout", "x"))
        fetch(token, 0)
        self.assertEqual(
            fetch(token, 2),
            {"page": [], "has_more": False, "error": "page_no out of range"},
        )

    def test_a_page_no_past_the_final_page_is_out_of_range(self):
        """A page_no past a finished job's final page returns the error "page_no out of range"."""
        token, _ = new_job(("stdout", "x"), finished=True)
        self.assertFalse(fetch(token, 0)["has_more"])
        self.assertEqual(
            fetch(token, 1),
            {"page": [], "has_more": False, "error": "page_no out of range"},
        )

    def test_an_unknown_job_token_returns_the_expired_token_error(self):
        """An unknown or expired job token returns {"page": [], "has_more": false, "error": "unknown or expired job_token"}."""
        self.assertEqual(
            fetch("no-such-token", 0),
            {"page": [], "has_more": False, "error": "unknown or expired job_token"},
        )

    def test_each_job_has_its_own_token_and_history(self):
        """Each job has its own token and history; fetching one job never drains another."""
        token_a, _ = new_job(("stdout", "from a"), finished=True)
        token_b, output_b = new_job(("stdout", "from b"), finished=True)
        self.assertNotEqual(token_a, token_b)
        result_a = fetch(token_a, 0)
        self.assertEqual(result_a["page"], [{"stream": "stdout", "text": "from a"}])
        self.assertEqual(output_b.qsize(), 2)  # b's output untouched
        result_b = fetch(token_b, 0)
        self.assertEqual(result_b["page"], [{"stream": "stdout", "text": "from b"}])


class RetentionTests(unittest.TestCase):
    def setUp(self):
        paging.clear_all()
        self.clock = FakeClock()
        patcher = mock.patch.object(paging, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_completed_history_stays_readable_for_the_retention_period(self):
        """A completed job's history stays readable for the retention period from its final page, and is gone after it."""
        token, output = new_job(("stdout", "x"))
        fetch(token, 0)
        self.clock.now += 500  # retention runs from the final page, not job start
        output.put(paging.SENTINEL)
        self.assertFalse(fetch(token, 1)["has_more"])
        self.clock.now += RETENTION_MS // 1000 - 1
        self.assertEqual(fetch(token, 0)["page"], [{"stream": "stdout", "text": "x"}])
        self.clock.now += 1
        self.assertEqual(fetch(token, 0)["error"], "unknown or expired job_token")

    def test_a_history_whose_final_page_is_never_fetched_is_kept_until_cleared(self):
        """A job whose final page is never fetched keeps its history until cleared."""
        token, output = new_job(("stdout", "x"))
        fetch(token, 0)
        output.put(paging.SENTINEL)  # finished, but nobody drains the end
        self.clock.now += 30 * 24 * 3600
        self.assertEqual(fetch(token, 0)["page"], [{"stream": "stdout", "text": "x"}])
        paging.clear_all()
        self.assertEqual(fetch(token, 0)["error"], "unknown or expired job_token")


if __name__ == "__main__":
    unittest.main()
