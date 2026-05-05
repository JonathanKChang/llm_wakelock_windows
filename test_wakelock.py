#!/usr/bin/env python3
"""Test: parse tcp_table_blob.bin and verify ESTABLISHED port 8001.

Uses ctypes casting to match the production GetExtendedTcpTable parsing.
The blob was captured with TCP_TABLE_MODULE_CONNECTIONS (type 7), so each
row is 160 bytes (32 bytes of TCP fields + 128 bytes module padding).

Run on Windows with the binary blob. Does not call iphlpapi at runtime.
"""

import ctypes
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
    with open(path, "rb") as f:
        data = f.read()
    buf = ctypes.create_string_buffer(data, len(data))
    num = struct.unpack("<I", data[:4])[0]
    return _parse_tcp_table(buf, num)


def print_summary(connections: list[dict]) -> None:
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
    path = sys.argv[1] if len(sys.argv) > 1 else "tcp_table_blob.bin"
    connections = parse_blob_file(path)
    print_summary(connections)
    found = any(
        c["state"] == MIB_TCP_STATE_ESTAB
        and (c["local_port"] == 8001 or c["remote_port"] == 8001)
        for c in connections
    )
    if not found:
        raise AssertionError("Port 8001 not found in ESTABLISHED connections")
    print()
    print("OK: Port 8001 confirmed in ESTABLISHED connections.")


if __name__ == "__main__":
    main()
