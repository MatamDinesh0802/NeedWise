"""The run script: Locust's processes do not outlive a run."""

import os
import sys
import time
from pathlib import Path

from workmind_load.run import run_in_group

# Starts a grandchild that would sleep for a minute (as a Locust worker waiting for its master's
# heartbeat), writes its pid, and exits with 3.
LEAVES_A_CHILD = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
sys.exit(3)
"""


def alive(pid: int) -> bool:
    """The grandchild is not ours: once its parent exits, init adopts and reaps it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_what_the_command_leaves_running_is_stopped(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    code = run_in_group([sys.executable, "-c", LEAVES_A_CHILD, str(pid_file)], tmp_path, grace_s=5)

    assert code == 3
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(child)
