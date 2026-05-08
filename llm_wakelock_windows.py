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
import subprocess
import threading
import queue
from typing import Protocol


class TcpConnectionSource(Protocol):
    """Protocol for TCP connection sources (Windows and WSL)."""

    def get_connections(self) -> list[dict]: ...


# Connection dict schema (returned by TcpConnectionSource.get_connections()):
#   state       (int)   — TCP state code (5 = ESTABLISHED)
#   local_addr  (str)   — dotted IPv4 address
#   local_port  (int)   — local port number
#   remote_addr (str)   — dotted IPv4 address
#   remote_port (int)   — remote port number
#   is_wsl      (bool)  — True if from WSL2, False if from Windows


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
    "enable_wsl_monitoring": False,
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
ENABLE_WSL_MONITORING = config["enable_wsl_monitoring"]

pprint.pprint(config, sort_dicts=False)

# ── WSL Subprocess State ──────────────────────────────────────────────────────
wsl_process = None
wsl_stdout_queue = queue.Queue()
wsl_stdout_thread = None
wsl_helper_deployed = False
wsl_warning_issued = False


def _wsl_run_command(cmd: str, check: bool = False) -> subprocess.CompletedProcess | None:
    """Run a command inside WSL via wsl.exe. Returns CompletedProcess or None on failure."""
    try:
        result = subprocess.run(
            ["wsl.exe", "-e", "bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if check and result.returncode != 0:
            return None
        return result
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def deploy_wsl_helper() -> bool:
    """Deploy the bash helper script to WSL at ~/bin/wsl_tcp_monitor.sh.

    Returns True if deployment succeeded, False otherwise.
    """
    global wsl_helper_deployed
    if wsl_helper_deployed:
        return True

    script_content = f"""while true; do
  cat /proc/net/tcp
  sleep {POLLING_INTERVAL}
done
"""
    # Use a heredoc to write the loop script inside WSL
    cmd = f'mkdir -p ~/bin && cat > ~/bin/wsl_tcp_monitor.sh << \'WSL_HELPER_EOF\'\n{script_content}WSL_HELPER_EOF\nchmod +x ~/bin/wsl_tcp_monitor.sh'
    result = _wsl_run_command(cmd)
    if result and result.returncode == 0:
        wsl_helper_deployed = True
        return True
    return False


def wsl_helper_available() -> bool:
    """Check if wsl.exe is available and the helper script exists in WSL."""
    # First check if wsl.exe exists
    if _wsl_run_command("echo ok") is None:
        return False
    # Check if the helper script exists
    result = _wsl_run_command("test -f ~/bin/wsl_tcp_monitor.sh && echo yes", check=False)
    return result is not None and result.stdout.strip() == "yes"


def _stdout_reader(process: subprocess.Popen) -> None:
    """Daemon thread that reads from subprocess stdout and puts lines into the queue."""
    try:
        for line in process.stdout:
            wsl_stdout_queue.put(line)
    except Exception:
        pass


def _start_wsl_subprocess() -> subprocess.Popen | None:
    """Spawn the persistent WSL subprocess. Returns Popen object or None on failure."""
    global wsl_stdout_thread
    try:
        proc = subprocess.Popen(
            ["wsl.exe", "-e", "bash", "~/bin/wsl_tcp_monitor.sh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # Start a daemon thread to read stdout into the queue
        wsl_stdout_thread = threading.Thread(target=_stdout_reader, args=(proc,), daemon=True)
        wsl_stdout_thread.start()
        return proc
    except (FileNotFoundError, OSError):
        return None


def _wsl_subprocess_alive(process: subprocess.Popen) -> bool:
    """Check if the WSL subprocess is still running."""
    return process is not None and process.poll() is None


def _ensure_wsl_subprocess() -> subprocess.Popen | None:
    """Ensure the WSL subprocess is running. Start or restart as needed."""
    global wsl_process
    if _wsl_subprocess_alive(wsl_process):
        return wsl_process
    # Process is dead or not started — try to start it
    wsl_process = _start_wsl_subprocess()
    return wsl_process


def _drain_wsl_output() -> list[str]:
    """Drain all available lines from the subprocess stdout queue (non-blocking)."""
    lines = []
    while not wsl_stdout_queue.empty():
        try:
            lines.append(wsl_stdout_queue.get_nowait())
        except queue.Empty:
            break
    return lines


# ── WSL TCP Parsing ──────────────────────────────────────────────────────────
_HEADER_KEYWORD = "local_address"


def _parse_proc_net_tcp_line(line: str) -> dict | None:
    """Parse a single /proc/net/tcp line into a connection dict.

    Returns None for header lines or malformed lines.
    Format: <sl> <local_addr>:<local_port> <remote_addr>:<remote_port> <state> ...
    All addresses and ports are hex-encoded.
    """
    line = line.strip()
    if not line or _HEADER_KEYWORD in line:
        return None

    parts = line.split()
    if len(parts) < 4:
        return None

    try:
        # parts[1] = local_addr:port, parts[2] = remote_addr:port, parts[3] = state
        local_hex = parts[1]
        remote_hex = parts[2]
        state_hex = parts[3]

        local_addr_hex, local_port_hex = local_hex.rsplit(":", 1)
        remote_addr_hex, remote_port_hex = remote_hex.rsplit(":", 1)

        local_port = int(local_port_hex, 16)
        remote_port = int(remote_port_hex, 16)
        state = int(state_hex, 16)

        # Convert hex addresses to dotted notation (little-endian)
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
            "pid": 0,  # WSL /proc/net/tcp doesn't expose PID
        }
    except (ValueError, IndexError):
        return None


def _tcp_state_is_active(state_hex: int) -> bool:
    """Return True only for ESTABLISHED (0x01), mirroring Windows iphlpapi logic."""
    return state_hex == 0x01


def get_wsl_tcp_connections() -> list[dict]:
    """Get active TCP connections from the WSL subprocess.

    Drains the subprocess pipe, parses lines, filters for active connections,
    and returns a list of connection dicts matching the Windows connection schema.
    Handles subprocess death by restarting and returning empty list for this cycle.
    """
    global wsl_process, wsl_warning_issued

    # Ensure subprocess is running
    if not _wsl_subprocess_alive(wsl_process):
        wsl_process = _start_wsl_subprocess()
        if wsl_process is None:
            # wsl.exe not available — log once and skip
            if not wsl_warning_issued:
                print("[wsl] wsl.exe not available, skipping WSL monitoring")
                wsl_warning_issued = True
            return []

    # Drain available lines from the queue
    lines = _drain_wsl_output()

    # If no lines and process is dead, restart
    if not lines and not _wsl_subprocess_alive(wsl_process):
        wsl_process = _start_wsl_subprocess()
        return []

    # Parse lines into connections
    connections = []
    for line in lines:
        parsed = _parse_proc_net_tcp_line(line)
        if parsed is None:
            continue
        if _tcp_state_is_active(parsed["state"]):
            connections.append(parsed)

    return connections


# ── WSL Monitoring Integration ───────────────────────────────────────────────

def is_wsl_monitored_active(connections: list[dict]) -> bool:
    """Check if any WSL connections match monitored ports."""
    for conn in connections:
        if conn["local_port"] in LOCAL_MONITORED_PORTS:
            return True
        if conn["remote_port"] in REMOTE_MONITORED_PORTS:
            return True
    return False


def has_active_connections(ssh_start_times: dict) -> bool:
    """Checks for active monitored-port or SSH connections (Windows + WSL2)."""
    # Windows connections
    windows_connections = get_established_tcp_connections()
    windows_active = is_monitored_active(windows_connections) or is_ssh_active(windows_connections, ssh_start_times)

    # WSL connections (only if enabled)
    wsl_active = False
    if ENABLE_WSL_MONITORING:
        wsl_connections = get_wsl_tcp_connections()
        wsl_active = is_wsl_monitored_active(wsl_connections) or is_ssh_active(wsl_connections, ssh_start_times)

    return windows_active or wsl_active

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


wakelock = False
ssh_start_times = {}

while True:
    # Get current connections
    windows_connections = get_established_tcp_connections()
    active = has_active_connections(ssh_start_times)
    now = datetime.datetime.now().isoformat()

    if active and not wakelock:
        acquire()
        # Collect relevant connection info from both Windows and WSL2
        relevant_strs = []
        for conn in windows_connections:
            if (conn["local_port"] in LOCAL_MONITORED_PORTS
                    or conn["remote_port"] in REMOTE_MONITORED_PORTS
                    or conn["local_port"] in LOCAL_SSH_PORTS
                    or conn["remote_port"] in REMOTE_SSH_PORTS):
                relevant_strs.append(f"  [win] {conn}")
        if ENABLE_WSL_MONITORING:
            wsl_connections = get_wsl_tcp_connections()
            for conn in wsl_connections:
                if (conn["local_port"] in LOCAL_MONITORED_PORTS
                        or conn["remote_port"] in REMOTE_MONITORED_PORTS
                        or conn["local_port"] in LOCAL_SSH_PORTS
                        or conn["remote_port"] in REMOTE_SSH_PORTS):
                    relevant_strs.append(f"  [wsl] {conn}")
        print(f"[{now}] Grabbing wakelock due to active connections:\n" + "\n".join(relevant_strs))
        wakelock = True

    elif not active and wakelock:
        release()
        print(f"[{now}] Releasing wakelock")
        wakelock = False

    time.sleep(POLLING_INTERVAL)
