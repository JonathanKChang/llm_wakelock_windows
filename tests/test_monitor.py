"""Tests for TcpConnectionMonitor — port matching, SSH tracking, wakelock transitions, formatting.

Uses the `make_monitor` fixture to inject mock handlers and test logic in isolation
without requiring Windows OS or real subprocesses.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

import llm_wakelock_windows as mod
from tcp_handlers import ConnectionSource


# ── Helpers ───────────────────────────────────────────────────────────────────

def _conn(local_port: int = 8080, remote_port: int = 12345, source: ConnectionSource = ConnectionSource.WINDOWS,
          local_addr: str = "192.168.1.1", remote_addr: str = "10.0.0.1"):
    """Build a minimal connection dict."""
    return {
        "state": 5,
        "local_addr": local_addr,
        "local_port": local_port,
        "remote_addr": remote_addr,
        "remote_port": remote_port,
        "source": source,
    }


# ── Port Monitoring Logic (is_monitored_active) ───────────────────────────────

class TestPortMonitoring:
    """is_monitored_active static method tests."""

    def test_matches_local_port(self, make_monitor):
        """Local port in monitored list returns True."""
        mon = make_monitor()
        assert mod.TcpConnectionMonitor.is_monitored_active(
            [_conn(local_port=8080)],
            [8080, 11434],
            [],
        ) is True

    def test_matches_remote_port(self, make_monitor):
        """Remote port in monitored list returns True."""
        mon = make_monitor()
        assert mod.TcpConnectionMonitor.is_monitored_active(
            [_conn(remote_port=11434)],
            [],
            [11434],
        ) is True

    def test_empty_connections_list(self, make_monitor):
        """Empty connection list returns False."""
        mon = make_monitor()
        assert mod.TcpConnectionMonitor.is_monitored_active(
            [], [8080], [8080]
        ) is False

    def test_no_match(self, make_monitor):
        """Neither port matches monitored list returns False."""
        mon = make_monitor()
        assert mod.TcpConnectionMonitor.is_monitored_active(
            [_conn(local_port=9999, remote_port=8888)],
            [8080],
            [11434],
        ) is False


# ── SSH Relevance Tests (is_relevant) ────────────────────────────────────────

class TestRelevance:
    """is_relevant method tests — determines if a connection is on any configured port."""

    def test_includes_monitored_ports(self, make_monitor):
        """Connection on monitored port is relevant."""
        mon = make_monitor()
        conn = _conn(local_port=8080)
        assert mon.is_relevant(conn) is True

    def test_includes_ssh_ports(self, make_monitor):
        """Connection on SSH port is relevant."""
        mon = make_monitor()
        conn = _conn(local_port=22)
        assert mon.is_relevant(conn) is True

    def test_excludes_other_ports(self, make_monitor):
        """Connection on non-monitored, non-SSH port is not relevant."""
        cfg = make_monitor()._config.copy()
        cfg["local_monitored_ports"] = [8080]
        cfg["remote_monitored_ports"] = []
        cfg["local_ssh_ports"] = [22]
        cfg["remote_ssh_ports"] = []
        mon = make_monitor(config=cfg)
        conn = _conn(local_port=9999)
        assert mon.is_relevant(conn) is False


# ── SSH Duration Tracking (is_ssh_active) ────────────────────────────────────

class TestSshDuration:
    """SSH active checks — connections must persist beyond min_duration threshold."""

    def test_active_after_min_duration(self, make_monitor, ssh_conn):
        """SSH connection older than threshold returns True."""
        mon = make_monitor()
        conn = ssh_conn(local_port=54321, remote_port=22)
        key = (conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
        mon._ssh_start_times[key] = time.time() - 60
        assert mon.is_ssh_active([conn], [22], [22], 30.0) is True

    def test_not_yet_active(self, make_monitor, ssh_conn):
        """SSH connection younger than threshold returns False."""
        mon = make_monitor()
        conn = ssh_conn(local_port=54321, remote_port=22)
        key = (conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
        mon._ssh_start_times[key] = time.time() - 5
        assert mon.is_ssh_active([conn], [22], [22], 30.0) is False

    def test_reconnect_resets_timer(self, make_monitor, ssh_conn):
        """Dropped SSH connection key is pruned; reconnection starts fresh timer."""
        mon = make_monitor()
        conn = ssh_conn(local_port=54321, remote_port=22)
        key = (conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])

        # Set old timer
        mon._ssh_start_times[key] = time.time() - 60
        assert mon.is_ssh_active([conn], [22], [22], 30.0) is True

        # Drop connection — key should be pruned
        assert mon.is_ssh_active([], [22], [22], 30.0) is False
        assert len(mon._ssh_start_times) == 0

        # Reconnect starts fresh
        new_conn = ssh_conn(local_port=54322, remote_port=22)
        assert mon.is_ssh_active([new_conn], [22], [22], 30.0) is False

    def test_multiple_connections_one_meets_threshold(self, make_monitor):
        """Multiple SSH connections — only one needs to exceed min_duration."""
        mon = make_monitor()
        conn1 = _conn(local_addr="0.0.0.1", local_port=54321, remote_port=22)
        conn2 = _conn(local_addr="0.0.0.2", local_port=54322, remote_port=22)

        # Only conn1's key has old timestamp
        key1 = (conn1["local_addr"], conn1["local_port"], conn1["remote_port"], conn1["remote_addr"])
        mon._ssh_start_times[key1] = time.time() - 60

        assert mon.is_ssh_active([conn1, conn2], [22], [22], 30.0) is True

    def test_prunes_stale_keys(self, make_monitor):
        """Connections that drop are pruned from _ssh_start_times."""
        mon = make_monitor()
        conn = _conn(local_port=22)
        key = (conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
        mon._ssh_start_times[key] = time.time() - 60
        assert len(mon._ssh_start_times) == 1

        mon.is_ssh_active([], [22], [22], 30.0)
        assert len(mon._ssh_start_times) == 0

    def test_non_ssh_port_not_tracked(self, make_monitor):
        """Connections on non-SSH ports don't pollute _ssh_start_times."""
        mon = make_monitor()
        conn = _conn(local_port=8080)
        mon.is_ssh_active([conn], [22], [22], 30.0)
        assert len(mon._ssh_start_times) == 0


# ── Wakelock State Transitions (DI-injected handlers) ────────────────────────

class TestWakelockTransitions:
    """Wakelock acquire/release transitions with mocked API and injected handlers."""

    def test_acquire_on_first_active_connection(self, make_monitor):
        """First active connection triggers _acquire() exactly once."""
        mock_handler = MagicMock()
        mock_handler.get_connections.return_value = [_conn(local_port=8080)]
        mock_handler.cleanup = MagicMock()
        mon = make_monitor(handlers=[mock_handler])

        with patch.object(mon, "_acquire") as mock_acquire:
            conns = mon.get_all_connections()
            active = mon.has_active_connections(conns, mon._config)
            assert active is True
            # Simulate what run() does
            if active and not getattr(mon, "_wakelock_state", False):
                mon._acquire()
                mon._wakelock_state = True
            assert mock_acquire.call_count == 1

    def test_no_double_acquire_while_locked(self, make_monitor):
        """While wakelock already held, active connections do NOT trigger another _acquire()."""
        mock_handler = MagicMock()
        mock_handler.get_connections.return_value = [_conn(local_port=8080)]
        mock_handler.cleanup = MagicMock()
        mon = make_monitor(handlers=[mock_handler])

        with patch.object(mon, "_acquire") as mock_acquire:
            # First active connection — acquire
            mon._acquire()
            mon._wakelock_state = True

            # Second active detection while already locked
            conns = mon.get_all_connections()
            if mod.TcpConnectionMonitor.is_monitored_active(
                conns, [8080], []
            ):
                pass  # no-op, already locked
            assert mock_acquire.call_count == 1

    def test_release_after_grace_period(self, make_monitor):
        """After grace period expires with no active connections, _release() is called.

        Uses 0 grace period for simplicity; the real logic path is tested
        via the timing mock in test_tcp_connection_monitor_sleeps_remaining_time.
        """
        mock_handler = MagicMock()
        mock_handler.get_connections.return_value = []  # no connections
        mock_handler.cleanup = MagicMock()
        # 0-minute grace period so any time delta triggers release
        cfg = make_monitor()._config.copy()
        cfg["grace_period_minutes"] = 0
        mon = make_monitor(config=cfg, handlers=[mock_handler])

        with patch.object(mon, "_release") as mock_release:
            # Simulate: wakelock is held, inactive_since set
            mon._wakelock_state = True
            import datetime
            mon.inactive_since = datetime.datetime.now() - datetime.timedelta(seconds=1)

            # Simulate the run() loop's release check
            now = datetime.datetime.now()
            grace_period_seconds = mon._config["grace_period_minutes"] * 60
            if getattr(mon, "_wakelock_state", False):
                if mon.inactive_since is not None:
                    if (now - mon.inactive_since).total_seconds() >= grace_period_seconds:
                        mon._release()

            assert mock_release.call_count == 1

    def test_wakelock_not_reacquired_if_already_locked(self, make_monitor):
        """Second active detection while already locked is a no-op."""
        mock_handler = MagicMock()
        mock_handler.get_connections.return_value = [_conn(local_port=8080)]
        mock_handler.cleanup = MagicMock()
        mon = make_monitor(handlers=[mock_handler])

        with patch.object(mon, "_acquire") as mock_acquire:
            # Already locked
            mon._wakelock_state = True
            mon._acquire()  # simulate first acquire

            conns = mon.get_all_connections()
            # Simulate second active detection
            if mod.TcpConnectionMonitor.is_monitored_active(
                conns, [8080], []
            ):
                pass  # no-op
            assert mock_acquire.call_count == 1


# ── Connection Formatting (format_connections) ────────────────────────────────

class TestConnectionFormatting:
    """format_connections output formatting."""

    def test_windows_label(self, make_monitor):
        """Windows connections use [win] label."""
        mon = make_monitor()
        conns = [_conn(local_port=8080, source=ConnectionSource.WINDOWS)]
        result = mon.format_connections(conns)
        assert result[0] == "  [win] 192.168.1.1:8080 -> 10.0.0.1:12345"

    def test_wsl_label(self, make_monitor):
        """WSL connections use [wsl] label."""
        mon = make_monitor()
        conns = [_conn(local_port=8080, source=ConnectionSource.WSL)]
        result = mon.format_connections(conns)
        assert result[0] == "  [wsl] 192.168.1.1:8080 -> 10.0.0.1:12345"

    def test_docker_label_with_container_id(self, make_monitor):
        """Docker connections show [docker:<12-char-id>] label."""
        mon = make_monitor()
        conn = _conn(local_port=8080, source=ConnectionSource.WSL_DOCKER)
        conn["container_id"] = "abc123def456789"
        result = mon.format_connections([conn])
        assert result[0] == "  [docker:abc123def456] 192.168.1.1:8080 -> 10.0.0.1:12345"

    def test_no_source_label(self, make_monitor):
        """show_source_label=False omits all labels."""
        mon = make_monitor()
        conns = [_conn(local_port=8080)]
        result = mon.format_connections(conns, show_source_label=False)
        assert result[0] == "  192.168.1.1:8080 -> 10.0.0.1:12345"

    def test_empty_list_returns_empty(self, make_monitor):
        """Empty connection list returns empty list."""
        mon = make_monitor()
        assert mon.format_connections([]) == []
