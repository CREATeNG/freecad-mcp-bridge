"""Requirements on turning the bridge off while jobs are running or waiting.

Each test name, and the first line of its docstring, states one requirement
as a user and an agent experience it, against a real bridge in a real
FreeCAD. The user's click on the toolbar toggle is made through the bridge
itself (execute_python), the one route a client has into FreeCAD's GUI; the
bridge is turned back on before the test ends. This module and
test_turning_on sort last, so a failure to turn it back on can fail only
their tests.
"""

import os
import tempfile
import threading
import time
import unittest

from tests.acceptance import mcp_client as mcp

# Job A: wait until job B is in line behind it, then have the toolbar toggle
# clicked while the script keeps the GUI responsive (processing events), as a
# user's click would land, then turn the bridge back on once the line is done.
HOLDING_JOB = """
import time
from PySide.QtCore import QCoreApplication, QTimer
from freecad.mcp_bridge import bridge

action = App.MCPBridgeAction  # the toolbar's On/Off action
# Precondition only: no public view of the job queue exists.
line = bridge._server._executor._jobs
deadline = time.monotonic() + 10
while line.empty() and time.monotonic() < deadline:
    time.sleep(0.1)
print('queued behind:', line.qsize())

QTimer.singleShot(0, action.trigger)
deadline = time.monotonic() + 2
while time.monotonic() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.05)
print('running after the click:', bridge.is_running())

QTimer.singleShot(
    1000, lambda: None if bridge.is_running() else action.trigger()
)
"""

# A running job: has the toolbar toggle clicked as job A does, runs on past
# the stop, records in the marker file that it finished, then turns the
# bridge back on, so the bridge answers again only once the job has ended.
# Run with `marker` (the marker file's path) defined ahead of it.
RUNNING_JOB = """
import os
import time
from PySide.QtCore import QCoreApplication, QTimer
from freecad.mcp_bridge import bridge

action = App.MCPBridgeAction  # the toolbar's On/Off action
print('before the click')
QTimer.singleShot(0, action.trigger)
deadline = time.monotonic() + 2
while time.monotonic() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.05)
state = f'running after the click: {bridge.is_running()}'
print(state)
with open(marker + '.part', 'w') as handle:
    handle.write(state)
os.replace(marker + '.part', marker)  # the marker appears whole

QTimer.singleShot(
    1000, lambda: None if bridge.is_running() else action.trigger()
)
"""


def wait_for_file(path, timeout_s=10):
    """Wait for a file to exist; True if it did in time."""
    deadline = time.monotonic() + timeout_s
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.1)
    return os.path.exists(path)


class TurningOffTests(unittest.TestCase):
    def test_a_job_still_in_line_when_the_bridge_is_turned_off_never_runs(self):
        """A job still waiting in line when the bridge is turned off never runs, and its token dies."""
        # This is the documented promise (design/job-lifecycle.md, "The
        # unhappy path"). A reading of executor.py suggests it does not hold
        # here: the dispatcher loop that is running job A takes job B next
        # from the same queue, whether or not the bridge was stopped
        # meanwhile. If this test fails with the marker present, that is the
        # mismatch between the documents and the code, not a test fault.
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "queued-job-ran")
            results = {}

            def submit(key, code):
                results[key] = mcp.execute(code)

            holder = threading.Thread(target=submit, args=("a", HOLDING_JOB))
            holder.start()
            time.sleep(1)
            queued = threading.Thread(
                target=submit, args=("b", f"open({marker!r}, 'w').close()")
            )
            queued.start()
            holder.join(timeout=60)
            queued.join(timeout=60)

            self.assertTrue(
                mcp.wait_until_reachable(30), "the bridge did not come back on"
            )
            mcp.run("pass")  # anything still in line has run by now
            held = mcp.text([results["a"]])
            self.assertIn("queued behind: 1", held)
            self.assertIn("running after the click: False", held)
            self.assertFalse(os.path.exists(marker), "the queued job ran")
            dead = mcp.get_page(results["b"]["job_token"], 0)
            self.assertEqual(dead["error"], "unknown or expired job_token")

    def test_a_running_job_finishes_when_the_bridge_is_turned_off_but_its_history_is_then_gone(self):
        """A job already running when the bridge is turned off runs to its end, but its undelivered output is lost and its token dies."""
        with tempfile.TemporaryDirectory() as folder:
            marker = os.path.join(folder, "running-job-finished")
            # A short response timeout returns the first page mid-job, so
            # the rest of the job's output is still undelivered at the stop.
            with mcp.preference("MaxResponseTimeoutSeconds", 1):
                first = mcp.execute(f"marker = {marker!r}\n" + RUNNING_JOB)
                self.assertTrue(
                    mcp.wait_until_reachable(30),
                    "the bridge did not come back on",
                )
            self.assertTrue(
                first["has_more"], "precondition: output still to come"
            )
            # Only once the job has ended (its marker written) is its
            # token checked, so the check cannot race the job.
            self.assertTrue(wait_for_file(marker), "the running job stopped")
            with open(marker, encoding="utf-8") as handle:
                state = handle.read()
            self.assertEqual(state, "running after the click: False")
        dead = mcp.get_page(first["job_token"], 0)
        self.assertEqual(dead["error"], "unknown or expired job_token")


if __name__ == "__main__":
    unittest.main()
