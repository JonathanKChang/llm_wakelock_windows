#!/usr/bin/env python3
"""Tests for llm_wakelock_windows TCP parsing and SSH tracking logic.

Part 1: parse_tcp_table_blob.bin — ctypes-based parsing of a captured
         TCP table blob (no iphlpapi calls at runtime).
Part 2: is_ssh_active — duration tracking, stale-entry pruning,
         reconnect detection. Pure Python, no Windows dependencies.
"""

import ctypes
import os
import socket
import struct
import sys
import time

try:
    import pytest
except ImportError:
    pytest = None  # type: ignore

MIB_TCP_STATE_ESTAB = 5
DWORD = ctypes.c_uint32


class MIB_TCPROW_MODULE(ctypes.Structure):
    _layout_ = 'ms'
    _fields_ = [
        ("dwState", DWORD),
        ("dwLocalAddr", DWORD),
        ("dwLocalPort", DWORD),
        ("dwRemoteAddr", DWORD),
        ("dwRemotePort", DWORD),
        ("dwOwningPid", DWORD),
        ("OwningModuleInfo", DWORD),
        ("Reserved", DWORD),
        ("Reserved2", ctypes.c_char * 128),
    ]


HEADER_SIZE = 8  # dwNumEntries + padding before first row
ROW_SIZE = 160   # MIB_TCPROW_MODULE size


def _parse_tcp_table(buf, num_entries: int) -> list[dict]:
    """Parse raw TCP buffer into structured connection data."""
    row_ptr = ctypes.cast(
        ctypes.addressof(buf) + HEADER_SIZE,
        ctypes.POINTER(MIB_TCPROW_MODULE),
    )
    rows = []
    for i in range(num_entries):
        row = row_ptr[i]
        lport = socket.ntohs(row.dwLocalPort & 0xFFFF)
        rport = socket.ntohs(row.dwRemotePort & 0xFFFF)
        laddr = socket.inet_ntoa(struct.pack("<I", row.dwLocalAddr))
        raddr = socket.inet_ntoa(struct.pack("<I", row.dwRemoteAddr))
        rows.append({
            "state": row.dwState,
            "local_addr": laddr,
            "local_port": lport,
            "remote_addr": raddr,
            "remote_port": rport,
            "is_wsl": False,
        })
    return rows


def parse_blob_file(path: str) -> list[dict]:
    """Read and parse TCP table blob from given file path."""
    with open(path, "rb") as f:
        data = f.read()
    buf = ctypes.create_string_buffer(data, len(data))
    num = struct.unpack("<I", data[:4])[0]
    return _parse_tcp_table(buf, num)


def print_summary(connections: list[dict]) -> None:
    """Print formatted summary of TCP connections, focusing on ESTABLISHED state."""
    estab = [c for c in connections if c["state"] == MIB_TCP_STATE_ESTAB]
    print(f"Total rows parsed: {len(connections)}")
    print(f"ESTABLISHED connections: {len(estab)}")
    print()
    if not estab:
        print("No ESTABLISHED connections found.")
        return
    print(f"{'Local Address':<20} {'Local Port':<10} {'Remote Address':<20} {'Remote Port':<10} {'is_wsl'}")
    print("-" * 76)
    for c in estab:
        print(f"{c['local_addr']:<20} {c['local_port']:<10} {c['remote_addr']:<20} {c['remote_port']:<10} {c['is_wsl']}")


def main():
    """Main entry point for processing TCP blob file and verifying monitored port."""
    path = sys.argv[1] if len(sys.argv) > 1 else "tcp_table_blob.bin"
    if not os.path.exists(path):
        print(f"SKIP: {path} not found — skipping blob test.")
        return
    connections = parse_blob_file(path)
    print_summary(connections)
    TEST_PORT = 8001
    found = any(
        c["state"] == MIB_TCP_STATE_ESTAB
        and (c["local_port"] == TEST_PORT or c["remote_port"] == TEST_PORT)
        for c in connections
    )
    if not found:
        raise AssertionError(f"Monitored port {TEST_PORT} not found in ESTABLISHED connections")
    print()
    print(f"OK: Monitored port {TEST_PORT} confirmed in ESTABLISHED connections.")


SSH_MIN_DURATION = 30.0


def _make_conn(is_wsl: bool, local_port, remote_port, remote_addr):
    """Build a minimal connection dict matching the new handler output schema."""
    return {
        "state": MIB_TCP_STATE_ESTAB,
        "local_addr": "0.0.0.0",
        "local_port": local_port,
        "remote_addr": remote_addr,
        "remote_port": remote_port,
        "is_wsl": is_wsl,
    }


def _is_ssh_active(connections, ssh_start_times, min_duration=SSH_MIN_DURATION):
    """Wrapper around production is_ssh_active for testing."""
    import llm_wakelock_windows as mod
    return mod.is_ssh_active(connections, ssh_start_times, [22], [22], min_duration)


# ── SSH Tracking Tests ────────────────────────────────────────────────────────

def test_ssh_active_after_min_duration():
    """Happy path: SSH connection older than threshold returns True."""
    ssh_start_times = {}
    conn = _make_conn(False, 54321, 22, "10.0.0.1")
    ssh_start_times[(54321, 22, "10.0.0.1")] = time.time() - 60
    assert _is_ssh_active([conn], ssh_start_times) is True


def test_ssh_not_yet_active():
    """Edge case: SSH connection younger than threshold returns False."""
    ssh_start_times = {}
    conn = _make_conn(False, 54321, 22, "10.0.0.1")
    ssh_start_times[(54321, 22, "10.0.0.1")] = time.time() - 5
    assert _is_ssh_active([conn], ssh_start_times) is False


def test_reconnect_new_pid_resets_timer():
    """Edge case: dropped+reconnected SSH with new PID starts fresh timer."""
    ssh_start_times = {}
    old_conn = _make_conn(False, 54321, 22, "10.0.0.1")
    ssh_start_times[(54321, 22, "10.0.0.1")] = time.time() - 60
    # Old connection still present — should return True
    assert _is_ssh_active([old_conn], ssh_start_times) is True
    # Connection drops: old key gets pruned
    assert _is_ssh_active([], ssh_start_times) is False
    assert len(ssh_start_times) == 0  # stale entry removed
    # New PID reconnects — fresh timer, too young
    new_conn = _make_conn(False, 54322, 22, "10.0.0.1")
    assert _is_ssh_active([new_conn], ssh_start_times) is False


def test_same_pid_reconnect_resets_timer():
    """Edge case: reconnects — timer resets because old key was pruned."""
    ssh_start_times = {}
    conn = _make_conn(False, 54321, 22, "10.0.0.1")
    ssh_start_times[(54321, 22, "10.0.0.1")] = time.time() - 60
    # Drop the connection — prunes the key
    assert _is_ssh_active([], ssh_start_times) is False
    assert len(ssh_start_times) == 0
    # Reconnects — treated as new connection
    assert _is_ssh_active([conn], ssh_start_times) is False


def test_non_ssh_port_ignored():
    """Failure case: connections on non-monitored ports don't pollute tracking."""
    ssh_start_times = {}
    conn = _make_conn(False, 54321, 443, "10.0.0.1")  # HTTPS, not SSH
    _is_ssh_active([conn], ssh_start_times)
    assert len(ssh_start_times) == 0


# ── WSL TCP Parsing Tests ────────────────────────────────────────────────────

_HEADER_KEYWORD = "local_address"


def _parse_proc_net_tcp_line(line: str) -> dict | None:
    """Wrapper around production WslTcpHandler._parse_proc_net_tcp_line for testing."""
    import llm_wakelock_windows as mod
    return mod.WslTcpHandler._parse_proc_net_tcp_line(line)


def _tcp_state_is_active(state_hex: int) -> bool:
    """Wrapper around production WslTcpHandler._tcp_state_is_active for testing."""
    import llm_wakelock_windows as mod
    return mod.WslTcpHandler._tcp_state_is_active(state_hex)


def test_parse_established_connection():
    """Parse an ESTABLISHED connection: local 8080, remote 8000."""
    # Linux TCP state 0x01 = ESTABLISHED
    line = "0:  00000000:1F90 0500000A:1F40 01 00000000:00000000 0:00000000 00000000     0 12345 2 0 10 0 0 10 0"
    result = _parse_proc_net_tcp_line(line)
    assert result is not None
    assert result["local_port"] == 8080    # 0x1F90
    assert result["remote_port"] == 8000   # 0x1F40
    assert result["state"] == 0x01         # ESTABLISHED
    assert result["local_addr"] == "0.0.0.0"
    assert result["remote_addr"] == "10.0.0.5"  # 0A000005 in little-endian
    assert result["is_wsl"] is True


def test_parse_time_wait():
    """Parse a TIME-WAIT connection."""
    line = "1:  0100007F:1F90 0100007F:C350 06 00000000:00000000 0:00000000 00000000     0     0 5 1 17 0 0 1 0"
    result = _parse_proc_net_tcp_line(line)
    assert result is not None
    assert result["state"] == 0x06         # TIME_WAIT
    assert result["local_port"] == 8080    # 0x1F90


def test_parse_close_wait():
    """Parse a CLOSE-WAIT connection."""
    line = "2:  0100007F:1F90 0100007F:C350 08 00000000:00000000 0:00000000 00000000     0     0 3 1 17 0 0 1 0"
    result = _parse_proc_net_tcp_line(line)
    assert result is not None
    assert result["state"] == 0x08         # CLOSE_WAIT


def test_parse_listen():
    """Parse a LISTEN connection."""
    line = "3:  00000000:1F90 00000000:0000 0A 00000000:00000000 0:00000000 00000000     0     0 1 0 10 0 0 1 0"
    result = _parse_proc_net_tcp_line(line)
    assert result is not None
    assert result["state"] == 0x0A         # LISTEN


def test_parse_header_line():
    """Header line should be skipped."""
    line = "  sl  local_address:remote_address st tx_queue:rx_queue:tm_when: retrnsmt uid timeout intrinsic"
    result = _parse_proc_net_tcp_line(line)
    assert result is None


def test_parse_empty_line():
    """Empty line should be skipped."""
    result = _parse_proc_net_tcp_line("")
    assert result is None


def test_parse_malformed_line():
    """Malformed line should return None without crashing."""
    result = _parse_proc_net_tcp_line("garbage data")
    assert result is None

    result = _parse_proc_net_tcp_line("0: incomplete")
    assert result is None


def test_tcp_state_is_active_all_codes():
    """Test all TCP state codes: only 0x01 (ESTABLISHED) is active, mirroring Windows."""
    # Only ESTABLISHED is active
    assert _tcp_state_is_active(0x01) is True

    # All other states are inactive
    inactive_states = [0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]
    for state in inactive_states:
        assert _tcp_state_is_active(state) is False, f"State {state:#04x} should be inactive"


def test_is_wsl_monitored_active():
    """Check connections against monitored ports using shared is_monitored_active."""
    import llm_wakelock_windows as mod
    LOCAL_MONITORED_PORTS = [8080, 11434]
    REMOTE_MONITORED_PORTS = [8080, 11434]

    # Connection on monitored local port
    conns = [{"local_port": 8080, "remote_port": 12345}]
    assert mod.is_monitored_active(conns, LOCAL_MONITORED_PORTS, REMOTE_MONITORED_PORTS) is True

    # Connection on monitored remote port
    conns = [{"local_port": 12345, "remote_port": 11434}]
    assert mod.is_monitored_active(conns, LOCAL_MONITORED_PORTS, REMOTE_MONITORED_PORTS) is True

    # No match
    conns = [{"local_port": 9999, "remote_port": 8888}]
    assert mod.is_monitored_active(conns, LOCAL_MONITORED_PORTS, REMOTE_MONITORED_PORTS) is False


def test_format_active_connections_with_labels():
    """Test format_active_connections with WSL/Windows labels."""
    import llm_wakelock_windows as mod
    format_active_connections = mod.format_active_connections

    # Mixed Windows and WSL connections with labels
    conns = [
        {"local_addr": "192.168.1.1", "local_port": 8080, "remote_addr": "10.0.0.1", "remote_port": 12345, "is_wsl": False},
        {"local_addr": "0.0.0.0", "local_port": 11434, "remote_addr": "172.17.0.1", "remote_port": 54321, "is_wsl": True},
    ]
    result = format_active_connections(conns, show_wsl_label=True)
    assert result[0] == "  [win] 192.168.1.1:8080 -> 10.0.0.1:12345"
    assert result[1] == "  [wsl] 0.0.0.0:11434 -> 172.17.0.1:54321"

    # Without labels
    result = format_active_connections(conns, show_wsl_label=False)
    assert result[0] == "  192.168.1.1:8080 -> 10.0.0.1:12345"
    assert result[1] == "  0.0.0.0:11434 -> 172.17.0.1:54321"
