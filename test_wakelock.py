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


import time

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


if __name__ == "__main__":
    main()
    print()
    print("Running SSH tracking tests…")
    test_ssh_active_after_min_duration()
    print("  PASS: test_ssh_active_after_min_duration")
    test_ssh_not_yet_active()
    print("  PASS: test_ssh_not_yet_active")
    test_reconnect_new_pid_resets_timer()
    print("  PASS: test_reconnect_new_pid_resets_timer")
    test_same_pid_reconnect_resets_timer()
    print("  PASS: test_same_pid_reconnect_resets_timer")
    test_non_ssh_port_ignored()
    print("  PASS: test_non_ssh_port_ignored")
    print("All SSH tests passed!")
