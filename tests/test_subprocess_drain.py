"""Additional tests for SubprocessDrain — lifecycle, restart logic, edge cases.

Existing drain behavior (sentinel parsing, caching) is tested in test_wakelock.py.
This file covers lifecycle transitions and timing edge cases not previously tested.
"""

from unittest.mock import MagicMock, patch

import pytest

import tcp_handlers
from tcp_handlers import SubprocessDrain


# ── Helpers ───────────────────────────────────────────────────────────────────

def _drain_config(**overrides):
    """Minimal config with optional overrides."""
    c = {
        "polling_interval": 1.0,
        "wsl_command_timeout": 10,
        "max_consecutive_failures": 3,
        "wsl_recovery_interval": 60,
    }
    c.update(overrides)
    return c


def _make_drain(**overrides):
    """Create a SubprocessDrain with minimal config."""
    return SubprocessDrain("echo test", _drain_config(**overrides))


class TestLifecycleStart:
    """SubprocessDrain start() behavior when WSL is not available."""

    def test_start_returns_none_when_wsl_not_running(self):
        """start() returns None when _wsl_running returns False (not mocked)."""
        # On Linux, _wsl_running always returns True in the real code,
        # but we mock it to test the path.
        drain = _make_drain()
        with patch.object(SubprocessDrain, "_wsl_running", return_value=False):
            result = drain.start()
        assert result is None

    def test_drain_returns_empty_when_wsl_not_running(self):
        """drain() returns [] when WSL is not running."""
        drain = _make_drain()
        with patch.object(SubprocessDrain, "_wsl_running", return_value=False):
            result = drain.drain()
        assert result == []
        assert drain._stopped is False  # not an error, just no WSL

    def test_drain_calls_start_if_process_never_started(self):
        """First drain() call triggers start() lazily."""
        drain = _make_drain()
        with patch.object(SubprocessDrain, "_wsl_running", return_value=True), \
             patch("tcp_handlers.subprocess.Popen") as mock_popen, \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            mock_proc = MagicMock()
            mock_proc.stdout = iter(["__SUBPROCESS_DRAIN__\n", "output\n", "__SUBPROCESS_DRAIN__\n"])
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            # Force start to be called (process is None)
            drain._process = None
            result = drain.drain()
        # start() should have been called
        assert mock_popen.called

    def test_stop_sets_stopped_flag(self):
        """stop() sets _stopped and halts the subprocess."""
        drain = _make_drain()
        with patch.object(SubprocessDrain, "_wsl_running", return_value=True), \
             patch("tcp_handlers.subprocess.Popen") as mock_popen, \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            drain._process = mock_proc
        drain.stop()
        assert drain._stopped is True


class TestRestartLogic:
    """SubprocessDrain restart cooldown and failure threshold checks."""

    def test_restart_not_called_within_cooldown(self):
        """If _last_restart_attempt was recent, no restart occurs regardless of failures."""
        import time
        drain = _make_drain(max_consecutive_failures=1)
        drain._consecutive_failures = 5
        drain._last_restart_attempt = time.time() - 1  # only 1 second ago, recovery_interval=60
        drain._restart_if_needed()
        # Should NOT have restarted (cooldown not elapsed)
        assert drain._consecutive_failures == 5

    def test_restart_not_called_below_failure_threshold(self):
        """Fewer failures than threshold does not trigger restart."""
        drain = _make_drain(max_consecutive_failures=3)
        drain._consecutive_failures = 2
        drain._last_restart_attempt = 0.0  # allow restart
        drain._restart_if_needed()
        assert drain._consecutive_failures == 2  # unchanged


class TestDrainEdgeCases:
    """Additional drain edge cases."""

    def test_consecutive_failures_counted_on_sentinel_miss_without_cache(self):
        """Sentinel miss with no prior cached output increments failures and returns []."""
        drain = _make_drain(max_consecutive_failures=2)
        # No sentinel in queue — will count as failure
        drain._queue.put("no sentinel here\n")
        result = drain.drain()
        assert result == []  # no cache
        assert drain._consecutive_failures == 1

    def test_owner_stored_and_used_in_logs(self):
        """SubprocessDrain stores owner string for log messages."""
        drain = SubprocessDrain("cmd", _drain_config(), owner="my custom owner")
        assert drain._owner == "my custom owner"

    def test_stop_while_running_terminates_process(self):
        """stop() terminates the subprocess process."""
        with patch.object(SubprocessDrain, "_wsl_running", return_value=True), \
             patch("tcp_handlers.subprocess.Popen") as mock_popen, \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            drain = _make_drain()
            drain._process = mock_proc
            drain._thread = MagicMock()
            drain._thread.is_alive.return_value = False
            drain.stop()
        assert mock_proc.terminate.called
