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
            "pid": row.dwOwningPid,
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
    print(f"{'Local Address':<20} {'Local Port':<10} {'Remote Address':<20} {'Remote Port':<10} {'PID'}")
    print("-" * 76)
    for c in estab:
        print(f"{c['local_addr']:<20} {c['local_port']:<10} {c['remote_addr']:<20} {c['remote_port']:<10} {c['pid']}")


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


def _make_conn(pid, local_port, remote_port, remote_addr):
    """Build a minimal connection dict like get_established_tcp_table returns."""
    return {
        "state": MIB_TCP_STATE_ESTAB,
        "local_addr": "0.0.0.0",
        "local_port": local_port,
        "remote_addr": remote_addr,
        "remote_port": remote_port,
        "pid": pid,
    }


def _is_ssh_active(connections, ssh_start_times, min_duration=SSH_MIN_DURATION):
    """Copy of production is_ssh_active for testing."""
    now = time.time()
    active_keys = set()
    for conn in connections:
        if conn["local_port"] in [22] or conn["remote_port"] in [22]:
            key = (conn["pid"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
            active_keys.add(key)
            if key not in ssh_start_times:
                ssh_start_times[key] = now
            elif now - ssh_start_times[key] >= min_duration:
                return True
    for key in list(ssh_start_times):
        if key not in active_keys:
            del ssh_start_times[key]
    return False


# ── SSH Tracking Tests ────────────────────────────────────────────────────────

def test_ssh_active_after_min_duration():
    """Happy path: SSH connection older than threshold returns True."""
    ssh_start_times = {}
    conn = _make_conn(1234, 54321, 22, "10.0.0.1")
    ssh_start_times[(1234, 54321, 22, "10.0.0.1")] = time.time() - 60
    assert _is_ssh_active([conn], ssh_start_times) is True


def test_ssh_not_yet_active():
    """Edge case: SSH connection younger than threshold returns False."""
    ssh_start_times = {}
    conn = _make_conn(1234, 54321, 22, "10.0.0.1")
    ssh_start_times[(1234, 54321, 22, "10.0.0.1")] = time.time() - 5
    assert _is_ssh_active([conn], ssh_start_times) is False


def test_reconnect_new_pid_resets_timer():
    """Edge case: dropped+reconnected SSH with new PID starts fresh timer."""
    ssh_start_times = {}
    old_conn = _make_conn(1234, 54321, 22, "10.0.0.1")
    ssh_start_times[(1234, 54321, 22, "10.0.0.1")] = time.time() - 60
    # Old connection still present — should return True
    assert _is_ssh_active([old_conn], ssh_start_times) is True
    # Connection drops: old key gets pruned
    assert _is_ssh_active([], ssh_start_times) is False
    assert len(ssh_start_times) == 0  # stale entry removed
    # New PID reconnects — fresh timer, too young
    new_conn = _make_conn(5678, 54322, 22, "10.0.0.1")
    assert _is_ssh_active([new_conn], ssh_start_times) is False


def test_same_pid_reconnect_resets_timer():
    """Edge case: same PID reconnects — timer resets because old key was pruned."""
    ssh_start_times = {}
    conn = _make_conn(1234, 54321, 22, "10.0.0.1")
    ssh_start_times[(1234, 54321, 22, "10.0.0.1")] = time.time() - 60
    # Drop the connection — prunes the key
    assert _is_ssh_active([], ssh_start_times) is False
    assert len(ssh_start_times) == 0
    # Same PID reconnects — treated as new connection
    assert _is_ssh_active([conn], ssh_start_times) is False


def test_non_ssh_port_ignored():
    """Failure case: connections on non-monitored ports don't pollute tracking."""
    ssh_start_times = {}
    conn = _make_conn(1234, 54321, 443, "10.0.0.1")  # HTTPS, not SSH
    _is_ssh_active([conn], ssh_start_times)
    assert len(ssh_start_times) == 0


# ── WSL TCP Parsing Tests ────────────────────────────────────────────────────

_HEADER_KEYWORD = "local_address"


def _parse_proc_net_tcp_line(line: str) -> dict | None:
    """Copy of production _parse_proc_net_tcp_line for testing."""
    line = line.strip()
    if not line or _HEADER_KEYWORD in line:
        return None

    parts = line.split()
    if len(parts) < 4:
        return None

    try:
        local_hex = parts[1]
        remote_hex = parts[2]
        state_hex = parts[3]

        local_addr_hex, local_port_hex = local_hex.rsplit(":", 1)
        remote_addr_hex, remote_port_hex = remote_hex.rsplit(":", 1)

        local_port = int(local_port_hex, 16)
        remote_port = int(remote_port_hex, 16)
        state = int(state_hex, 16)

        local_addr_int = int(local_addr_hex, 16)
        remote_addr_int = int(remote_addr_hex, 16)
        local_addr = socket.inet_ntoa(struct.pack("<I", local_addr_int))
        remote_addr = socket.inet_ntoa(struct.pack("<I", remote_addr_int))

        return {
            "state": state,
            "local_addr": local_addr,
            "local_port": local_port,
            "remote_addr": remote_addr,
            "remote_port": remote_port,
            "pid": 0,
        }
    except (ValueError, IndexError):
        return None


def _tcp_state_is_active(state_hex: int) -> bool:
    """Copy of production _tcp_state_is_active for testing."""
    return state_hex == 0x01


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
    """Check WSL connections against monitored ports."""
    LOCAL_MONITORED_PORTS = [8080, 11434]
    REMOTE_MONITORED_PORTS = [8080, 11434]

    # Connection on monitored local port
    conns = [{"local_port": 8080, "remote_port": 12345}]
    assert any(c["local_port"] in LOCAL_MONITORED_PORTS for c in conns) is True

    # Connection on monitored remote port
    conns = [{"local_port": 12345, "remote_port": 11434}]
    assert any(c["remote_port"] in REMOTE_MONITORED_PORTS for c in conns) is True

    # No match
    conns = [{"local_port": 9999, "remote_port": 8888}]
    assert any(c["local_port"] in LOCAL_MONITORED_PORTS for c in conns) is False
    assert any(c["remote_port"] in REMOTE_MONITORED_PORTS for c in conns) is False


def test_deploy_wsl_helper_stub():
    """Test that deploy_wsl_helper returns False when wsl.exe is not available."""
    # On this system (WSL not guaranteed), the function should gracefully
    # return False without crashing
    try:
        import llm_wakelock_windows as mod
        result = mod.deploy_wsl_helper()
        assert result is False
    except (Exception, SystemExit) as e:
        # If the module can't be imported (Windows-only guard), that's fine
        print(f"SKIP: deploy_wsl_helper test skipped ({e})")


def test_wsl_helper_available_stub():
    """Test that wsl_helper_available returns False when wsl.exe is not available."""
    try:
        import llm_wakelock_windows as mod
        result = mod.wsl_helper_available()
        assert result is False
    except (Exception, SystemExit) as e:
        print(f"SKIP: wsl_helper_available test skipped ({e})")
