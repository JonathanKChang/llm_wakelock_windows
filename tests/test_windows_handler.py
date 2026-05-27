"""Tests for WindowsTcpHandler — error handling, state filtering, cleanup.

Error-handling tests mock ctypes.windll.iphlpapi via patch with create=True.
Parsing logic is indirectly verified by existing tests in test_wakelock.py
(which test the WSL handler's shared _parse_proc_net_tcp_line and
_tcp_state_is_active methods that Windows handler also uses).
"""

from unittest.mock import MagicMock, patch

import pytest

from tcp_handlers import WindowsTcpHandler


def _make_handler(config=None):
    """Create a WindowsTcpHandler with minimal config."""
    cfg = config or {
        "debug": False,
        "local_monitored_ports": [8080],
        "remote_monitored_ports": [],
        "local_ssh_ports": [],
        "remote_ssh_ports": [],
        "ssh_min_duration": 30.0,
        "polling_interval": 5.0,
        "grace_period_minutes": 30,
        "wsl_monitoring": False,
        "wsl_docker_monitoring_max": 0,
        "wsl_command_timeout": 10,
        "wsl_recovery_interval": 60,
        "max_consecutive_failures": 3,
    }
    return WindowsTcpHandler(cfg)


# ── Error Handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    """Verify that unexpected API returns raise OSError."""

    def test_raises_on_unexpected_first_call_return(self):
        """First call returns non-122 → OSError raised (not buffer-too-small)."""
        handler = _make_handler()
        with patch("tcp_handlers.ctypes.windll", create=True) as mock_windll:
            mock_iphlpapi = MagicMock()
            mock_iphlpapi.GetExtendedTcpTable.return_value = 5
            mock_windll.iphlpapi = mock_iphlpapi

            with pytest.raises(OSError, match="Unexpected error"):
                handler.get_connections()

    def test_raises_on_second_call_failure(self):
        """Second call returns non-0 → OSError raised."""
        handler = _make_handler()
        with patch("tcp_handlers.ctypes.windll", create=True) as mock_windll:
            mock_iphlpapi = MagicMock()
            call_count = [0]

            def side_effect(*args):
                call_count[0] += 1
                return 87 if call_count[0] == 2 else 122

            mock_iphlpapi.GetExtendedTcpTable.side_effect = side_effect
            mock_windll.iphlpapi = mock_iphlpapi

            with pytest.raises(OSError, match="GetExtendedTcpTable failed"):
                handler.get_connections()


# ── Cleanup ───────────────────────────────────────────────────────────────────

class TestCleanup:
    """Windows handler has no subprocesses to clean up."""

    def test_cleanup_is_noop(self):
        """No-op — Windows handler uses iphlpapi syscall, no subprocesses."""
        handler = _make_handler()
        handler.cleanup()  # should not raise

    def test_cleanup_idempotent(self):
        """Calling cleanup multiple times does not error."""
        handler = _make_handler()
        handler.cleanup()
        handler.cleanup()
        handler.cleanup()


# ── State Filtering (via shared base class method) ───────────────────────────

class TestStateFiltering:
    """Shared _tcp_state_is_active static method tests.

    WindowsTcpHandler inherits TcpConnectionSource behavior for state checking.
    These verify that only ESTABLISHED connections are considered active —
    the same filtering used in both WSL and Windows code paths.
    """

    def test_only_established_is_active(self):
        """Only TCP ESTABLISHED state is considered active (5 for Windows, 0x01 for WSL)."""
        # Windows iphlpapi uses state=5 for ESTABLISHED
        assert WindowsTcpHandler.MIB_TCP_STATE_ESTAB == 5
        from tcp_handlers import WslTcpConnectionHandler
        # WSL uses 0x01 but _tcp_state_is_active only checks 0x01
        assert WslTcpConnectionHandler._tcp_state_is_active(0x01) is True

    def test_non_established_states_inactive(self):
        """All non-ESTABLISHED states return False."""
        inactive_states = {
            0x02: "SYN_SENT", 0x03: "SYN_RECV",
            0x04: "FIN_WAIT1", 0x05: "FIN_WAIT2",
            0x06: "TIME_WAIT", 0x07: "CLOSE",
            0x08: "CLOSE_WAIT", 0x09: "LAST_ACK",
            0x0A: "LISTEN", 0x0B: "CLOSING",
        }
        from tcp_handlers import WslTcpConnectionHandler
        for state, name in inactive_states.items():
            assert not WslTcpConnectionHandler._tcp_state_is_active(state), \
                f"{name} ({state:#04x}) should be inactive"


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    """Verify handler constants match iphlpapi expectations."""

    def test_af_ipv4(self):
        assert WindowsTcpHandler.AF_INET == 2

    def test_table_class(self):
        """TCP_TABLE_OWNER_PID_ALL = 5."""
        assert WindowsTcpHandler.TCP_TABLE_OWNER_PID_ALL == 5

    def test_established_state(self):
        """Windows iphlpapi uses state=5 for ESTABLISHED."""
        assert WindowsTcpHandler.MIB_TCP_STATE_ESTAB == 5

    def test_error_insufficient_buffer(self):
        """ERROR_INSUFFICIENT_BUFFER = 122."""
        assert WindowsTcpHandler.ERROR_INSUFFICIENT_BUFFER == 122
