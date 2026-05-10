"""
Monitors configured TCP ports and manages system wake lock on Windows.

This is a generic port monitoring tool: it watches for established TCP
connections on configured local and remote ports, and prevents the system
from sleeping while any monitored connection is active.

Common use cases include:
  - LLM service monitoring (e.g., llama.cpp on 8080, Ollama on 11434)
  - SSH session keep-alive
  - Any long-running service that should keep the machine awake

Configuration is done via `config.toml` in the same directory.
"""
import sys
import time
import ctypes
import socket
import struct
import datetime
import tomllib
import os
import pprint

if sys.platform != "win32":
    print("Error: this script requires Windows", file=sys.stderr)
    sys.exit(1)

# ── Configuration ──────────────────────────────────────────────────────────────
# Defaults — override by placing config.toml next to this script.
DEFAULTS = {
    "local_monitored_ports": [8080, 11434],
    "remote_monitored_ports": [8080, 11434],
    "local_ssh_ports": [],
    "remote_ssh_ports": [],
    "ssh_min_duration": 30.0,
    "polling_interval": 5.0,
    "grace_period_minutes": 0,
}

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
with open(_config_path, "rb") as f:
    user_cfg = tomllib.load(f)

config = {**DEFAULTS, **user_cfg}

LOCAL_MONITORED_PORTS = config["local_monitored_ports"]
REMOTE_MONITORED_PORTS = config["remote_monitored_ports"]
LOCAL_SSH_PORTS = config["local_ssh_ports"]
REMOTE_SSH_PORTS = config["remote_ssh_ports"]
SSH_MIN_DURATION = config["ssh_min_duration"]
POLLING_INTERVAL = config["polling_interval"]
GRACE_PERIOD_MINUTES = config["grace_period_minutes"]

pprint.pprint(config, sort_dicts=False)

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
    time.sleep(1)
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

def is_relevant(conn):
    return (
        conn["local_port"] in LOCAL_MONITORED_PORTS
        or conn["remote_port"] in REMOTE_MONITORED_PORTS
        or conn["local_port"] in LOCAL_SSH_PORTS
        or conn["remote_port"] in REMOTE_SSH_PORTS
    )

wakelock = False
inactive_since = None
ssh_start_times = {}
GRACE_PERIOD_SECONDS = GRACE_PERIOD_MINUTES * 60

while True:
    connections = get_established_tcp_connections()
    active = is_monitored_active(connections) or is_ssh_active(connections, ssh_start_times)
    now = datetime.datetime.now()

    if active:
        inactive_since = None
        if not wakelock:
            relevant_str = "\n".join(map(str, filter(is_relevant, connections)))
            print(f"[{now}] Acquiring wakelock due to active connections:\n{relevant_str}")
            acquire()
            wakelock = True

    elif wakelock:
        if inactive_since is None:
            print(f"[{now}] No more active connections")
            inactive_since = now

        if now - inactive_since >= GRACE_PERIOD_SECONDS:
            print(f"[{now}] Releasing wakelock")
            release()
            wakelock = False
            inactive_since = None

    time.sleep(POLLING_INTERVAL)