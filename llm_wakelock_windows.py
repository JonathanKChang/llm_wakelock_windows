"""
Monitors configured TCP ports and manages system wake lock on Windows.

This is a generic port monitoring tool: it watches for established TCP
connections on configured local and remote ports, and prevents the system
from sleeping while any monitored connection is active.

Common use cases include:
  - LLM service monitoring (e.g., llama.cpp on 8080, Ollama on 11434)
  - SSH session keep-alive
  - Any long-running service that should keep the machine awake

Configuration is done by editing the port lists and duration thresholds
near the top of this file.
"""
import sys
import time
import ctypes
import socket
import struct
import datetime

if sys.platform != "win32":
    print("Error: this script requires Windows", file=sys.stderr)
    sys.exit(1)

# Ports to monitor for active connections.
# Add or remove ports as needed for your services.
# Defaults: 8080 (llama.cpp server), 11434 (Ollama)
LOCAL_MONITORED_PORTS = [8080, 11434]
REMOTE_MONITORED_PORTS = [8080, 11434]
LOCAL_SSH_PORTS = []
REMOTE_SSH_PORTS = []
SSH_MIN_DURATION = 30.0
POLLING_INTERVAL = 1.0

# Startup: print configuration parameters
print("Configuration:")
print(f"LOCAL_MONITORED_PORTS={LOCAL_MONITORED_PORTS}")
print(f"REMOTE_MONITORED_PORTS={REMOTE_MONITORED_PORTS}")
print(f"LOCAL_SSH_PORTS={LOCAL_SSH_PORTS}")
print(f"REMOTE_SSH_PORTS={REMOTE_SSH_PORTS}")
print(f"SSH_MIN_DURATION={SSH_MIN_DURATION}")
print(f"POLLING_INTERVAL={POLLING_INTERVAL}")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
ERROR_INSUFFICIENT_BUFFER = 122
MIB_TCP_STATE_ESTAB = 5


def acquire():
    """Acquires system wake lock to prevent sleep."""
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    )


def release():
    """Resets idle timer then releases system wake lock"""

    # Pulse the wake-lock flag briefly to reset idle timer
    ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED)
    
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def get_established_tcp_connections():
    """Retrieve all established TCP connections with owner PID details."""
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


def is_monitored_active(connections):
    """Checks if any monitored-port connections are active (instant detection)."""
    for conn in connections:
        if conn["local_port"] in LOCAL_MONITORED_PORTS:
            return True
        if conn["remote_port"] in REMOTE_MONITORED_PORTS:
            return True
    return False


def is_ssh_active(connections, ssh_start_times):
    """Checks if any SSH connections have been active for at least SSH_MIN_DURATION.

    Tracks each connection by (pid, local_port, remote_port, remote_addr).
    Prunes stale entries when a connection drops, so a reconnect starts a
    fresh timer. Does not account for connection state changes beyond the
    initial ESTABLISHED detection.
    """
    now = time.time()
    active_keys = set()
    for conn in connections:
        if conn["local_port"] in LOCAL_SSH_PORTS or conn["remote_port"] in REMOTE_SSH_PORTS:
            # Include remote_addr in the key to distinguish SSH sessions to
            # different hosts, even if they share the same port numbers.
            key = (conn["pid"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
            active_keys.add(key)
            if key not in ssh_start_times:
                ssh_start_times[key] = now
            elif now - ssh_start_times[key] >= SSH_MIN_DURATION:
                return True
    # Prune stale entries for connections that are no longer established
    for key in list(ssh_start_times):
        if key not in active_keys:
            del ssh_start_times[key]
    return False


def has_active_connections(ssh_start_times):
    """Checks for active monitored-port or SSH connections."""
    connections = get_established_tcp_connections()
    return is_monitored_active(connections) or is_ssh_active(connections, ssh_start_times)


wakelock = False
ssh_start_times = {}

while True:
    # Get current connections
    connections = get_established_tcp_connections()
    active = is_monitored_active(connections) or is_ssh_active(connections, ssh_start_times)
    now = datetime.datetime.now().isoformat()

    if active and not wakelock:
        acquire()
        # Print date/time and relevant connection info
        relevant_str = "\n".join([
            str(conn) for conn in connections
            if conn["local_port"] in LOCAL_MONITORED_PORTS
               or conn["remote_port"] in REMOTE_MONITORED_PORTS
               or conn["local_port"] in LOCAL_SSH_PORTS
               or conn["remote_port"] in REMOTE_SSH_PORTS
        ])
        print(f"[{now}] Grabbing wakelock due to active connections: \n{relevant_str}")
        wakelock = True

    elif not active and wakelock:
        release()
        print(f"[{now}] Releasing wakelock")
        wakelock = False

    time.sleep(POLLING_INTERVAL)
