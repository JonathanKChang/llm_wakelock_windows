import sys
import time
import ctypes
import socket
import struct

if sys.platform != "win32":
    print("Error: this script requires Windows", file=sys.stderr)
    sys.exit(1)

LOCAL_LLM_PORTS = [8001]
REMOTE_LLM_PORTS = [8001]
LOCAL_SSH_PORTS = [22]
REMOTE_SSH_PORTS = [22]
SSH_MIN_DURATION = 5.0
POLLING_INTERVAL = 1.0

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
ERROR_INSUFFICIENT_BUFFER = 122
MIB_TCP_STATE_ESTAB = 5


def acquire():
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    )


def release():
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def get_established_tcp_connections():
    iphlpapi = ctypes.windll.iphlpapi

    size = ctypes.c_ulong(0)
    ret = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if ret != ERROR_INSUFFICIENT_BUFFER:
        raise OSError(f"Unexpected error querying TCP table size: {ret}")

    buf = ctypes.create_string_buffer(size.value)
    ret = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0
    )
    if ret != 0:
        raise OSError(f"GetExtendedTcpTable failed: {ret}")

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", ctypes.c_ulong),
            ("dwLocalAddr", ctypes.c_ulong),
            ("dwLocalPort", ctypes.c_ulong),
            ("dwRemoteAddr", ctypes.c_ulong),
            ("dwRemotePort", ctypes.c_ulong),
            ("dwOwningPid", ctypes.c_ulong),
        ]

    num_entries = ctypes.c_ulong.from_buffer(buf).value
    row_start = ctypes.addressof(buf) + ctypes.sizeof(ctypes.c_ulong)
    row_ptr = ctypes.cast(row_start, ctypes.POINTER(MIB_TCPROW_OWNER_PID))

    connections = []
    for i in range(num_entries):
        row = row_ptr[i]
        if row.dwState != MIB_TCP_STATE_ESTAB:
            continue
        connections.append({
            "state": row.dwState,
            "local_addr": socket.inet_ntoa(struct.pack("<L", row.dwLocalAddr)),
            "local_port": socket.ntohs(row.dwLocalPort & 0xFFFF),
            "remote_addr": socket.inet_ntoa(struct.pack("<L", row.dwRemoteAddr)),
            "remote_port": socket.ntohs(row.dwRemotePort & 0xFFFF),
            "pid": row.dwOwningPid,
        })
    return connections


def is_llm_active(connections):
    for conn in connections:
        if conn["local_port"] in LOCAL_LLM_PORTS:
            return True
        if conn["remote_port"] in REMOTE_LLM_PORTS:
            return True
    return False


def is_ssh_active(connections, ssh_start_times):
    now = time.time()
    for conn in connections:
        if conn["local_port"] in LOCAL_SSH_PORTS or conn["remote_port"] in REMOTE_SSH_PORTS:
            key = (conn["pid"], conn["local_port"], conn["remote_port"])
            if key not in ssh_start_times:
                ssh_start_times[key] = now
            elif now - ssh_start_times[key] >= SSH_MIN_DURATION:
                return True
    return False


def has_active_connections(ssh_start_times):
    connections = get_established_tcp_connections()
    return is_llm_active(connections) or is_ssh_active(connections, ssh_start_times)


wakelock = False
ssh_start_times = {}

while True:
    active = has_active_connections(ssh_start_times)

    if active and not wakelock:
        acquire()
        wakelock = True

    elif not active and wakelock:
        release()
        wakelock = False

    time.sleep(POLLING_INTERVAL)
