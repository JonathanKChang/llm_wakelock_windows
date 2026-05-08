#!/usr/bin/env python3
"""
Dump raw TCP table buffer from iphlpapi (Windows only).

Writes the raw GetExtendedTcpTable buffer to tcp_table_blob.bin
for low-level analysis of the TCP table structure.
"""

import sys
import ctypes
import os

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "tcp_table_blob.bin")

# Windows-only
if sys.platform != 'win32':
    print("This script is designed to run on Windows only.", file=sys.stderr)
    sys.exit(1)

# Load iphlpapi.dll
try:
    iphlpapi = ctypes.WinDLL('iphlpapi')
except Exception as e:
    print(f"Failed to load iphlpapi.dll: {e}", file=sys.stderr)
    sys.exit(1)

# Constants
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 7  # TCP_TABLE_OWNER_PID_ALL


def main():
    output = DEFAULT_OUTPUT

    # Get required buffer size
    size = ctypes.c_ulong(0)
    ret = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
    if ret != 122:  # ERROR_INSUFFICIENT_BUFFER
        print(f"GetExtendedTcpTable size query failed with error: {ret}",
              file=sys.stderr)
        sys.exit(1)

    # Allocate and fill buffer
    buf = ctypes.create_string_buffer(size.value)
    ret = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
    if ret != 0:
        err = ctypes.WinError(ret)
        print(f"GetExtendedTcpTable failed: {ret} ({err})", file=sys.stderr)
        sys.exit(1)

    # Write raw binary to file
    with open(output, "wb") as f:
        f.write(buf.raw[:size.value])
    print(f"Wrote {size.value} bytes to {output}")


if __name__ == '__main__':
    main()
