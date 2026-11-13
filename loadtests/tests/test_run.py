"""The run script: Locust's processes do not outlive a run."""

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from needwise_load.run import run_in_group, stop_group

# Starts a grandchild that would sleep for a minute (as a Locust worker waiting for its master's
# heartbeat), writes its pid, and exits with 3.
LEAVES_A_CHILD = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
sys.exit(3)
"""


def alive(pid: int) -> bool:
    """The grandchild is not ours: once its parent exits, init adopts and reaps it. Until then a
    dead one is a zombie, which macOS refuses to signal (EPERM): not alive either."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
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


def test_a_group_that_can_no_longer_be_signalled_counts_as_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS answers EPERM for a group whose last member died but is not reaped yet."""
    calls: list[int] = []

    def killpg(group: int, sig: int) -> None:
        calls.append(sig)
        if sig == 0:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", killpg)

    stop_group(12345, grace_s=5)

    assert calls == [signal.SIGTERM, 0]  # no SIGKILL, no error
