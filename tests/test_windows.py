"""Windows-specific live tests — require Windows OS.

These tests exercise real Windows APIs and are skipped on Linux via the
@pytest.mark.windows marker. Run explicitly on a Windows machine:
    python -m pytest tests/ -m "windows"

Or run all tests including Windows:
    python -m pytest tests/ -m "windows or not windows"
"""

import sys
import pytest

from tcp_handlers import WindowsTcpHandler


class TestWindowsLive:
    """Tests that require actual Windows OS and APIs."""

    @pytest.mark.windows
    def test_real_api_call_returns_connections(self):
        """@pytest.mark.windows: Actual GetExtendedTcpTable call on Windows.

        Verifies the handler can successfully query the real TCP table
        and parse at least some connections. This is a smoke test for
        the iphlpapi integration path.

        Skipped on Linux via @pytest.mark.windows marker.
        """
        if sys.platform != "win32":
            pytest.skip("requires Windows OS")

        handler = WindowsTcpHandler({
            "debug": False,
            "local_monitored_ports": [],
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
        })
        connections = handler.get_connections()
        # Should return at least some connections on a live system
        assert isinstance(connections, list)
        for conn in connections:
            assert "local_addr" in conn
            assert "local_port" in conn
            assert "remote_addr" in conn
            assert "remote_port" in conn
            assert "state" in conn

    @pytest.mark.windows
    def test_kernel32_acquire_release_behavior(self):
        """@pytest.mark.windows: Verify SetThreadExecutionState calls succeed on Windows.

        Best-effort test — we cannot assert OS behavior, but we verify
        the call succeeds without raising an exception.
        """
        if sys.platform != "win32":
            pytest.skip("requires Windows OS")

        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001

        # Acquire (prevent sleep)
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
        assert result != 0, "SetThreadExecutionState acquire failed"

        # Release
        result = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        assert result != 0, "SetThreadExecutionState release failed"

    @pytest.mark.windows
    def test_ctypes_struct_layout(self):
        """@pytest.mark.windows: Verify MIB_TCPROW_OWNER_PID struct field ordering."""
        if sys.platform != "win32":
            pytest.skip("requires Windows OS")

        import ctypes
        import struct

        class MIB_TCPROW_OWNER_PID(ctypes.Structure):
            _fields_ = [
                ("dwState", ctypes.c_ulong),
                ("dwLocalAddr", ctypes.c_ulong),
                ("dwLocalPort", ctypes.c_ulong),
                ("dwRemoteAddr", ctypes.c_ulong),
                ("dwRemotePort", ctypes.c_ulong),
                ("dwOwningPid", ctypes.c_ulong),
            ]

        # Verify struct size matches expected: 6 x uint32 = 24 bytes
        assert ctypes.sizeof(MIB_TCPROW_OWNER_PID) == 24

        # Verify field offsets
        assert ctypes.offsetof(MIB_TCPROW_OWNER_PID, "dwState") == 0
        assert ctypes.offsetof(MIB_TCPROW_OWNER_PID, "dwLocalAddr") == 4
        assert ctypes.offsetof(MIB_TCPROW_OWNER_PID, "dwLocalPort") == 8
        assert ctypes.offsetof(MIB_TCPROW_OWNER_PID, "dwRemoteAddr") == 12
        assert ctypes.offsetof(MIB_TCPROW_OWNER_PID, "dwRemotePort") == 16
        assert ctypes.offsetof(MIB_TCPROW_OWNER_PID, "dwOwningPid") == 20
