#!/usr/bin/env python3
"""
Test script to print TCP connection information using iphlpapi on Windows.
This script is intended to be run on Windows to verify the iphlpapi output.
"""

import sys
import ctypes
import socket
import struct

# Check if running on Windows
if sys.platform != 'win32':
    print("This script is designed to run on Windows only.")
    sys.exit(1)

# Load iphlpapi.dll
try:
    iphlpapi = ctypes.WinDLL('IpHLpApi')
except Exception as e:
    print(f"Failed to load iphlpapi.dll: {e}")
    sys.exit(1)

# Constants
AF_INET = 2  # IPv4
AF_INET6 = 23 # IPv6 (we are only interested in IPv4 for now)
MIB_TCP_STATE_CLOSED = 1
MIB_TCP_STATE_LISTEN = 2
MIB_TCP_STATE_SYN_SENT = 3
MIB_TCP_STATE_SYN_RCVD = 4
MIB_TCP_STATE_ESTAB = 5
MIB_TCP_STATE_FIN_WAIT1 = 6
MIB_TCP_STATE_FIN_WAIT2 = 7
MIB_TCP_STATE_CLOSE_WAIT = 8
MIB_TCP_STATE_CLOSING = 9
MIB_TCP_STATE_LAST_ACK = 10
MIB_TCP_STATE_TIME_WAIT = 11
MIB_TCP_STATE_DELETE_TCB = 12

# Map state ID to string for readability
TCP_STATE_NAMES = {
    MIB_TCP_STATE_CLOSED: "CLOSED",
    MIB_TCP_STATE_LISTEN: "LISTEN",
    MIB_TCP_STATE_SYN_SENT: "SYN_SENT",
    MIB_TCP_STATE_SYN_RCVD: "SYN_RCVD",
    MIB_TCP_STATE_ESTAB: "ESTAB",
    MIB_TCP_STATE_FIN_WAIT1: "FIN_WAIT1",
    MIB_TCP_STATE_FIN_WAIT2: "FIN_WAIT2",
    MIB_TCP_STATE_CLOSE_WAIT: "CLOSE_WAIT",
    MIB_TCP_STATE_CLOSING: "CLOSING",
    MIB_TCP_STATE_LAST_ACK: "LAST_ACK",
    MIB_TCP_STATE_TIME_WAIT: "TIME_WAIT",
    MIB_TCP_STATE_DELETE_TCB: "DELETE_TCB"
}

# Structures for iphlpapi
class MIB_TCPROW(ctypes.Structure):
    _fields_ = [
        ("dwState", ctypes.DWORD),
        ("dwLocalAddr", ctypes.DWORD),
        ("dwLocalPort", ctypes.DWORD),
        ("dwRemoteAddr", ctypes.DWORD),
        ("dwRemotePort", ctypes.DWORD),
        ("dwOwningPid", ctypes.DWORD)
    ]

class MIB_TCPTABLE(ctypes.Structure):
    _fields_ = [
        ("dwNumEntries", ctypes.DWORD),
        ("table", MIB_TCPROW * 1)  # Variable length array
    ]

# Function prototype for GetExtendedTcpTable
# We'll use the version that returns a table with the owning PID.
GetExtendedTcpTable = iphlpapi.GetExtendedTcpTable
GetExtendedTcpTable.argtypes = [
    ctypes.POINTER(ctypes.c_void),  # pTcpTable
    ctypes.POINTER(ctypes.c_ulong), # pdwSize
    ctypes.c_bool,                  # bOrder
    ctypes.c_ulong,                 # lAf
    ctypes.c_ulong,                 # TableType
    ctypes.c_ulong                  # Reserved
]
GetExtendedTcpTable.restype = ctypes.c_ulong

# Table types
TCP_TABLE_OWNER_PID_ALL = 1
TCP_TABLE_OWNER_PID_LISTENER = 2
TCP_TABLE_OWNER_PID_CONNECTIONS = 3
TCP_TABLE_OWNER_MODULE_ALL = 4
TCP_TABLE_OWNER_MODULE_LISTENER = 5
TCP_TABLE_OWNER_MODULE_CONNECTIONS = 6

def main():
    # We want all TCP connections with owner PID (IPv4)
    af = AF_INET
    table_type = TCP_TABLE_OWNER_PID_ALL
    reserved = 0
    order = True  # sorted by local address

    # First call to get the required buffer size
    size = ctypes.c_ulong(0)
    ret = GetExtendedTcpTable(None, ctypes.byref(size), order, af, table_type, reserved)
    if ret != 0:  # ERROR_INSUFFICIENT_BUFFER is expected
        if ret != 122:  # ERROR_INSUFFICIENT_BUFFER
            print(f"Initial GetExtendedTcpTable call failed with error: {ret}")
            return

    # Allocate buffer
    buffer = (ctypes.c_byte * size.value)()
    tcp_table = ctypes.cast(buffer, ctypes.POINTER(MIB_TCPTABLE))

    # Second call to get the actual data
    ret = GetExtendedTcpTable(tcp_table, ctypes.byref(size), order, af, table_type, reserved)
    if ret != 0:
        print(f"GetExtendedTcpTable call failed with error: {ret}")
        return

    # Parse the table
    num_entries = tcp_table.contents.dwNumEntries
    rows = tcp_table.contents.table

    print(f"Number of TCP entries: {num_entries}")
    print("-" * 80)
    print(f"{'Local Address':<20} {'Local Port':<12} {'Remote Address':<20} {'Remote Port':<12} {'State':<12} {'Owning PID'}")
    print("-" * 80)

    for i in range(num_entries):
        row = rows[i]
        # Convert DWORDs to IP addresses and ports (note: port is in network byte order)
        local_addr = socket.inet_ntoa(struct.pack('<L', row.dwLocalAddr))
        local_port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        remote_addr = socket.inet_ntoa(struct.pack('<L', row.dwRemoteAddr))
        remote_port = socket.ntohs(row.dwRemotePort & 0xFFFF)
        state = row.dwState
        pid = row.dwOwningPid

        # Only print established connections
        if state == MIB_TCP_STATE_ESTAB:
            state_str = TCP_STATE_NAMES.get(state, str(state))
            print(f"{local_addr:<20} {local_port:<12} {remote_addr:<20} {remote_port:<12} {state_str:<12} {pid}")

if __name__ == '__main__':
    main()
