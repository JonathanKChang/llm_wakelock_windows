#!/usr/bin/env python3
"""Tests for TCP connection monitoring logic.

- SSH tracking: duration tracking, stale-entry pruning, reconnect detection
- WSL TCP parsing: /proc/net/tcp line parsing
- Port monitoring: is_monitored_active, format_connections
- Docker: container discovery, no-containers handling, docker labels
"""

from unittest.mock import patch, MagicMock
import time

import llm_wakelock_windows as mod
import tcp_handlers
from tcp_handlers import WslTcpConnectionHandler as _M, ConnectionSource as _C, WslTcpHandler, WslDockerManager


def _ssh_conn(local_addr, local_port, remote_port, remote_addr):
    """Build a minimal connection dict for SSH tests."""
    return {
        "state": 5,
        "local_addr": local_addr,
        "local_port": local_port,
        "remote_addr": remote_addr,
        "remote_port": remote_port,
        "is_wsl": False,
    }


def _monitor():
    """Create a minimal monitor instance for testing shared methods."""
    return mod.TcpConnectionMonitor({
        "local_monitored_ports": [8080, 11434],
        "remote_monitored_ports": [8080, 11434],
        "local_ssh_ports": [22],
        "remote_ssh_ports": [22],
        "ssh_min_duration": 30.0,
        "polling_interval": 5.0,
        "wsl_monitoring": False,
        "wsl_docker_monitoring_max": 0,
    })


# ── SSH Tracking Tests ────────────────────────────────────────────────────────


def test_ssh_active_after_min_duration():
    """Happy path: SSH connection older than threshold returns True."""
    mon = _monitor()
    conn = _ssh_conn("0.0.0.0", 54321, 22, "10.0.0.1")
    mon._ssh_start_times[(conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])] = time.time() - 60
    assert mon.is_ssh_active([conn], [22], [22], 30.0) is True


def test_ssh_not_yet_active():
    """Edge case: SSH connection younger than threshold returns False."""
    mon = _monitor()
    conn = _ssh_conn("0.0.0.0", 54321, 22, "10.0.0.1")
    mon._ssh_start_times[(conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])] = time.time() - 5
    assert mon.is_ssh_active([conn], [22], [22], 30.0) is False


def test_reconnect_new_pid_resets_timer():
    """Edge case: dropped+reconnected SSH with new PID starts fresh timer."""
    mon = _monitor()
    old_conn = _ssh_conn("0.0.0.0", 54321, 22, "10.0.0.1")
    key = (old_conn["local_addr"], old_conn["local_port"], old_conn["remote_port"], old_conn["remote_addr"])
    mon._ssh_start_times[key] = time.time() - 60
    assert mon.is_ssh_active([old_conn], [22], [22], 30.0) is True
    assert mon.is_ssh_active([], [22], [22], 30.0) is False
    assert len(mon._ssh_start_times) == 0
    new_conn = _ssh_conn("0.0.0.0", 54322, 22, "10.0.0.1")
    assert mon.is_ssh_active([new_conn], [22], [22], 30.0) is False


def test_same_pid_reconnect_resets_timer():
    """Edge case: reconnects — timer resets because old key was pruned."""
    mon = _monitor()
    conn = _ssh_conn("0.0.0.0", 54321, 22, "10.0.0.1")
    key = (conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
    mon._ssh_start_times[key] = time.time() - 60
    assert mon.is_ssh_active([], [22], [22], 30.0) is False
    assert len(mon._ssh_start_times) == 0
    assert mon.is_ssh_active([conn], [22], [22], 30.0) is False


def test_non_ssh_port_ignored():
    """Failure case: connections on non-monitored ports don't pollute tracking."""
    mon = _monitor()
    conn = _ssh_conn("0.0.0.0", 54321, 443, "10.0.0.1")
    mon.is_ssh_active([conn], [22], [22], 30.0)
    assert len(mon._ssh_start_times) == 0


# ── WSL TCP Parsing Tests ────────────────────────────────────────────────────


def _parse_line(line: str):
    """Wrapper around production WslTcpConnectionHandler._parse_proc_net_tcp_line."""
    return WslTcpHandler._parse_proc_net_tcp_line(line)


def _tcp_state_is_active(state_hex: int) -> bool:
    """Wrapper around production WslTcpConnectionHandler._tcp_state_is_active."""
    return WslTcpHandler._tcp_state_is_active(state_hex)


def test_parse_established_connection():
    """Parse an ESTABLISHED connection: local 8080, remote 8000."""
    line = "0:  00000000:1F90 0500000A:1F40 01 00000000:00000000 0:00000000 00000000     0 12345 2 0 10 0 0 10 0"
    result = _parse_line(line)
    assert result is not None
    assert result["local_port"] == 8080
    assert result["remote_port"] == 8000
    assert result["state"] == _M.ESTABLISHED
    assert result["local_addr"] == "0.0.0.0"
    assert result["remote_addr"] == "10.0.0.5"


def test_parse_time_wait():
    """Parse a TIME-WAIT connection."""
    line = "1:  0100007F:1F90 0100007F:C350 06 00000000:00000000 0:00000000 00000000     0     0 5 1 17 0 0 1 0"
    result = _parse_line(line)
    assert result is not None
    assert result["state"] == 0x06  # TIME_WAIT


def test_parse_close_wait():
    """Parse a CLOSE-WAIT connection."""
    line = "2:  0100007F:1F90 0100007F:C350 08 00000000:00000000 0:00000000 00000000     0     0 3 1 17 0 0 1 0"
    result = _parse_line(line)
    assert result is not None
    assert result["state"] == 0x08  # CLOSE_WAIT


def test_parse_listen():
    """Parse a LISTEN connection."""
    line = "3:  00000000:1F90 00000000:0000 0A 00000000:00000000 0:00000000 00000000     0     0 1 0 10 0 0 1 0"
    result = _parse_line(line)
    assert result is not None
    assert result["state"] == 0x0A  # LISTEN


def test_parse_header_line():
    """Header line should be skipped."""
    line = "  sl  local_address:remote_address st tx_queue:rx_queue:tm_when: retrnsmt uid timeout intrinsic"
    assert _parse_line(line) is None


def test_parse_empty_line():
    """Empty line should be skipped."""
    assert _parse_line("") is None


def test_parse_malformed_line():
    """Malformed line should return None without crashing."""
    assert _parse_line("garbage data") is None
    assert _parse_line("0: incomplete") is None


def test_tcp_state_is_active_all_codes():
    """Test all TCP state codes: only ESTABLISHED (0x01) is active."""
    assert _tcp_state_is_active(_M.ESTABLISHED) is True
    for state in [0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]:
        assert _tcp_state_is_active(state) is False, f"State {state:#04x} should be inactive"


# ── Port Monitoring Tests ────────────────────────────────────────────────────


def test_is_monitored_active():
    """Check connections against monitored ports."""
    mon = _monitor()
    assert mon.is_monitored_active([{"local_port": 8080, "remote_port": 12345, "source": _C.WINDOWS}], [8080, 11434], [8080, 11434]) is True
    assert mon.is_monitored_active([{"local_port": 12345, "remote_port": 11434, "source": _C.WINDOWS}], [8080, 11434], [8080, 11434]) is True
    assert mon.is_monitored_active([{"local_port": 9999, "remote_port": 8888, "source": _C.WINDOWS}], [8080, 11434], [8080, 11434]) is False


def test_format_connections_with_labels():
    """Test format_connections with source labels."""
    mon = _monitor()
    conns = [
        {"local_addr": "192.168.1.1", "local_port": 8080, "remote_addr": "10.0.0.1", "remote_port": 12345, "source": _C.WINDOWS},
        {"local_addr": "0.0.0.0", "local_port": 11434, "remote_addr": "172.17.0.1", "remote_port": 54321, "source": _C.WSL},
    ]
    result = mon.format_connections(conns, show_source_label=True)
    assert result[0] == "  [win] 192.168.1.1:8080 -> 10.0.0.1:12345"
    assert result[1] == "  [wsl] 0.0.0.0:11434 -> 172.17.0.1:54321"
    result = mon.format_connections(conns, show_source_label=False)
    assert result[0] == "  192.168.1.1:8080 -> 10.0.0.1:12345"
    assert result[1] == "  0.0.0.0:11434 -> 172.17.0.1:54321"


def test_format_connections_docker_label():
    """Test docker:container_id label in format_connections."""
    mon = _monitor()
    conns = [
        {"local_addr": "172.17.0.2", "local_port": 5432, "remote_addr": "10.0.0.1", "remote_port": 80,
         "source": _C.WSL_DOCKER, "container_id": "abc123def456"},
    ]
    result = mon.format_connections(conns, show_source_label=True)
    assert result[0] == "  [docker:abc123def456] 172.17.0.2:5432 -> 10.0.0.1:80"


# ── Docker Tests ─────────────────────────────────────────────────────────────


def test_docker_container_discovery_respects_max():
    """Docker discovery caps handlers at wsl_docker_monitoring_max."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "abc123\ndef456\nghi789\n"
    with patch("tcp_handlers.subprocess.run", return_value=mock_result), \
         patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
        config = {"wsl_docker_monitoring_max": 2, "polling_interval": 5.0}
        manager = WslDockerManager(config)
        assert len(manager._handlers) == 2


def test_docker_discovery_runs_on_interval():
    """Discovery runs every N get_connections() calls."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "abc123\ndef456\n"
    with patch("tcp_handlers.subprocess.run", return_value=mock_result), \
         patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
        config = {"wsl_docker_monitoring_max": 5, "polling_interval": 5.0, "wsl_docker_discovery_interval": 3}
        manager = WslDockerManager(config)
        initial_count = len(manager._handlers)
        # Calls 1-2: no new discovery
        manager.get_connections()
        manager.get_connections()
        assert manager._discovery_cycle == 2
        # Call 3: discovery runs
        manager.get_connections()
        assert manager._discovery_cycle == 3
        assert len(manager._handlers) == initial_count  # same containers, no new ones


def test_docker_handler_no_containers():
    """Docker manager returns empty list when no containers are running."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    with patch("tcp_handlers.subprocess.run", return_value=mock_result), \
         patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
        config = {"wsl_docker_monitoring_max": 5, "polling_interval": 5.0}
        manager = WslDockerManager(config)
        assert manager.get_connections() == []
        assert manager.unavailable is False  # no containers != unavailable
