"""Serve the installed bridge to the acceptance tests, then quit.

Run inside a FreeCAD GUI process (third launch, after test_verify.py):

  freecad scripts/acceptance_serve.py

Turns the bridge on through the real toolbar action, writes the bridge's port
to the ready file, and returns to FreeCAD's own event loop, so jobs run exactly
as they do for a user. The acceptance tests run outside FreeCAD, as an MCP
client would. FreeCAD quits once the stop file appears, or fails after the
hard timeout.

Environment:
  ACCEPTANCE_READY_FILE     Written with the bridge's port once it is listening (required)
  ACCEPTANCE_STOP_FILE      Its appearance makes FreeCAD quit (required)
  ACCEPTANCE_TIMEOUT_S      Hard limit on serving, in seconds (default: 600)
  RELEASE_INSTALL_GUI_DELAY_MS        Delay before work starts (default: 3000)
  RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS  Wait for auto-injected toolbar (default: 15000)
"""

from __future__ import annotations

import os
import sys
import time

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import test_install_common as common

POLL_MS = 250


def _write_ready_file(path: str, port: int) -> None:
    """Write the port atomically, so a reader never sees a partial file."""
    partial = f"{path}.partial"
    with open(partial, "w", encoding="utf-8") as handle:
        handle.write(f"{port}\n")
    os.replace(partial, path)


def _watch_for_stop(stop_file: str, timeout_s: float) -> None:
    import FreeCAD as App
    from PySide.QtCore import QTimer

    deadline = time.monotonic() + timeout_s
    timer = QTimer()

    def check():
        if os.path.exists(stop_file):
            timer.stop()
            common.log_notice("Stop file seen; quitting")
            common.log_group_end()
            common.quit_freecad(0)
        elif time.monotonic() > deadline:
            timer.stop()
            common.fail(f"No stop file after {timeout_s:.0f} s; quitting")

    timer.timeout.connect(check)
    timer.start(POLL_MS)
    # Keep the timer alive after this script's namespace is released.
    App.AcceptanceStopTimer = timer


def _run() -> None:
    from freecad.mcp_bridge import config

    common.log_group_start("Serve the bridge for acceptance tests")
    common.log_opengl_info()
    ready_file = common.env("ACCEPTANCE_READY_FILE", "")
    stop_file = common.env("ACCEPTANCE_STOP_FILE", "")
    if not ready_file or not stop_file:
        common.fail("ACCEPTANCE_READY_FILE and ACCEPTANCE_STOP_FILE must be set")

    common.start_bridge()
    port = config.port()
    _write_ready_file(ready_file, port)
    common.log_notice(f"Bridge ready on 127.0.0.1:{port}; serving until {stop_file} appears")
    _watch_for_stop(stop_file, float(common.env("ACCEPTANCE_TIMEOUT_S", "600")))
    # Return to FreeCAD's event loop: jobs run there, as they do for a user.


common.LOG_PREFIX = "[acceptance_serve]"
common.schedule_main(_run)
