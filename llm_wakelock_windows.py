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
import subprocess
import threading
import queue
import pprint
from enum import Enum
from typing import Protocol


class ConnectionSource(Enum):
    WINDOWS = 0
    WSL = 1
    WSL_DOCKER = 2


# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULTS = {
    "local_monitored_ports": [8080, 11434],
    "remote_monitored_ports": [8080, 11434],
    "local_ssh_ports": [],
    "remote_ssh_ports": [],
    "ssh_min_duration": 30.0,
    "polling_interval": 5.0,
    "wsl_monitoring": False,
}


class TcpConnectionSource(Protocol):
    """Protocol for TCP connection sources (Windows and WSL)."""

    def get_connections(self) -> list[dict]: ...
    @property
    def unavailable(self) -> str | None: ...


class TcpConnectionMonitor:
    """Named constants shared across handlers and the main loop."""

    ESTABLISHED = 0x01
    MIB_TCP_STATE_ESTAB = 5
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    AF_INET = 2
    TCP_TABLE_OWNER_PID_ALL = 5
    ERROR_INSUFFICIENT_BUFFER = 122

    def __init__(self, config: dict) -> None:
        self._config = config
        self._handlers: list[TcpConnectionSource] = [WindowsTcpHandler(config)]
        if config["wsl_monitoring"]:
            self._handlers.append(WslTcpHandler(config))
        self._ssh_start_times: dict = {}

    def is_monitored_active(self, connections: list[dict], local_ports: list[int], remote_ports: list[int]) -> bool:
        """Check if any connection matches monitored ports (works with any source)."""
        for conn in connections:
            if conn["local_port"] in local_ports:
                return True
            if conn["remote_port"] in remote_ports:
                return True
        return False

    def is_ssh_active(self, connections: list[dict], local_ports: list[int], remote_ports: list[int], min_duration: float) -> bool:
        """Check if any SSH connections have been active for at least min_duration.

        Tracks each connection by (local_addr, local_port, remote_port, remote_addr).
        Prunes stale entries when a connection drops.
        """
        now = time.time()
        active_keys = set()
        for conn in connections:
            if conn["local_port"] in local_ports or conn["remote_port"] in remote_ports:
                key = (conn["local_addr"], conn["local_port"], conn["remote_port"], conn["remote_addr"])
                active_keys.add(key)
                if key not in self._ssh_start_times:
                    self._ssh_start_times[key] = now
                elif now - self._ssh_start_times[key] >= min_duration:
                    return True
        for key in list(self._ssh_start_times):
            if key not in active_keys:
                del self._ssh_start_times[key]
        return False

    def format_active_connections(self, connections: list[dict], show_source_label: bool = True) -> list[str]:
        """Format active connection dicts into log strings."""
        _labels = {ConnectionSource.WINDOWS: "win", ConnectionSource.WSL: "wsl", ConnectionSource.WSL_DOCKER: "docker"}
        strs = []
        for conn in connections:
            src = conn.get("source", ConnectionSource.WINDOWS)
            label = _labels.get(src, "?")
            cid = conn.get("container_id")
            if cid and src == ConnectionSource.WSL_DOCKER:
                label = f"docker:{cid[:12]}"
            prefix = (f"[{label}] " if show_source_label else "")
            strs.append(f"  {prefix}{conn['local_addr']}:{conn['local_port']} -> {conn['remote_addr']}:{conn['remote_port']}")
        return strs

    def _get_all_connections(self) -> list[dict]:
        """Collect connections from all handlers into a single list."""
        all_conns: list[dict] = []
        for handler in self._handlers:
            all_conns.extend(handler.get_connections())
        return all_conns

    def has_active_connections(self) -> bool:
        """Check for active monitored-port or SSH connections from all sources."""
        all_conns = self._get_all_connections()
        return self.is_monitored_active(all_conns, self._config["local_monitored_ports"], self._config["remote_monitored_ports"]) or \
               self.is_ssh_active(all_conns, self._config["local_ssh_ports"], self._config["remote_ssh_ports"], self._config["ssh_min_duration"])

    def _acquire(self) -> None:
        """Acquires system wake lock to prevent sleep."""
        ctypes.windll.kernel32.SetThreadExecutionState(
            TcpConnectionMonitor.ES_CONTINUOUS | TcpConnectionMonitor.ES_SYSTEM_REQUIRED
        )

    def _release(self) -> None:
        """Resets idle timer then releases system wake lock."""
        ctypes.windll.kernel32.SetThreadExecutionState(TcpConnectionMonitor.ES_SYSTEM_REQUIRED)
        ctypes.windll.kernel32.SetThreadExecutionState(TcpConnectionMonitor.ES_CONTINUOUS)

    def run(self) -> None:
        """Main loop: monitor connections and manage wakelock."""
        if sys.platform != "win32":
            print("Error: this script requires Windows", file=sys.stderr)
            sys.exit(1)

        wakelock = False
        while True:
            active = self.has_active_connections()
            now = datetime.datetime.now().isoformat()

            if active and not wakelock:
                self._acquire()
                relevant_conns = [
                    conn for conn in self._get_all_connections()
                    if (conn["local_port"] in self._config["local_monitored_ports"]
                        or conn["remote_port"] in self._config["remote_monitored_ports"]
                        or conn["local_port"] in self._config["local_ssh_ports"]
                        or conn["remote_port"] in self._config["remote_ssh_ports"])
                ]
                print(f"[{now}] Grabbing wakelock due to active connections:\n" + "\n".join(self.format_active_connections(relevant_conns)))
                wakelock = True

            elif not active and wakelock:
                self._release()
                print(f"[{now}] Releasing wakelock")
                wakelock = False

            time.sleep(self._config["polling_interval"])


# Connection dict schema (returned by TcpConnectionSource.get_connections()):
#   state         (int)   — TCP state code (5 = ESTABLISHED)
#   local_addr    (str)   — dotted IPv4 address
#   local_port    (int)   — local port number
#   remote_addr   (str)   — dotted IPv4 address
#   remote_port   (int)   — remote port number
#   source        (ConnectionSource) — WINDOWS, WSL, or WSL_DOCKER
#   container_id  (str)   — Docker container short ID (only for WSL_DOCKER)


class WindowsTcpHandler:
    """Handles Windows TCP connection retrieval via iphlpapi."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._unavailable: str | None = None

    @property
    def unavailable(self) -> str | None:
        return self._unavailable

    def get_connections(self) -> list[dict]:
        """Retrieve all established TCP connections from Windows iphlpapi."""
        iphlpapi = ctypes.windll.iphlpapi
        size = ctypes.c_ulong(0)
        ret = iphlpapi.GetExtendedTcpTable(
            None, ctypes.byref(size), True, TcpConnectionMonitor.AF_INET, TcpConnectionMonitor.TCP_TABLE_OWNER_PID_ALL, 0
        )
        if ret != TcpConnectionMonitor.ERROR_INSUFFICIENT_BUFFER:
            raise OSError(f"Unexpected error querying TCP table size: {ret}")

        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedTcpTable(
            buf, ctypes.byref(size), True, TcpConnectionMonitor.AF_INET, TcpConnectionMonitor.TCP_TABLE_OWNER_PID_ALL, 0
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
            if row.dwState != TcpConnectionMonitor.MIB_TCP_STATE_ESTAB:
                continue
            connections.append({
                "state": row.dwState,
                "local_addr": socket.inet_ntoa(struct.pack("<L", row.dwLocalAddr)),
                "local_port": socket.ntohs(row.dwLocalPort & 0xFFFF),
                "remote_addr": socket.inet_ntoa(struct.pack("<L", row.dwRemoteAddr)),
                "remote_port": socket.ntohs(row.dwRemotePort & 0xFFFF),
                "source": ConnectionSource.WINDOWS,
            })
        return connections


class WslTcpConnectionHandler:
    """Base class for WSL TCP connection retrieval via persistent subprocess."""

    ESTABLISHED = 0x01
    TCP_STATES = {
        0x01: "ESTABLISHED", 0x02: "SYN_SENT", 0x03: "SYN_RECV",
        0x04: "FIN_WAIT1", 0x05: "FIN_WAIT2", 0x06: "TIME_WAIT",
        0x07: "CLOSE", 0x08: "CLOSE_WAIT", 0x09: "LAST_ACK",
        0x0A: "LISTEN", 0x0B: "CLOSING",
    }

    def __init__(self, config: dict, command: str) -> None:
        self._config = config
        self._unavailable: str | None = None
        self._command = command
        self._process: subprocess.Popen | None = None
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._warning_issued = False
        self._header_seen = False

    @property
    def unavailable(self) -> str | None:
        return self._unavailable

    def _run_command(self, cmd: str, check: bool = False) -> subprocess.CompletedProcess | None:
        """Run a command inside WSL via wsl.exe using sh -c."""
        try:
            result = subprocess.run(
                ["wsl.exe", "-e", "sh", "-c", cmd],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if check and result.returncode != 0:
                return None
            return result
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    def _stdout_reader(self, process: subprocess.Popen) -> None:
        """Daemon thread that reads subprocess stdout into the queue."""
        try:
            for line in process.stdout:
                self._stdout_queue.put(line)
        except Exception:
            pass

    def _start_subprocess(self) -> subprocess.Popen | None:
        """Spawn the persistent WSL subprocess."""
        try:
            proc = subprocess.Popen(
                ["wsl.exe", "-e", "sh", "-c", self._command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._stdout_thread = threading.Thread(
                target=self._stdout_reader, args=(proc,), daemon=True
            )
            self._stdout_thread.start()
            return proc
        except (FileNotFoundError, OSError):
            return None

    def _subprocess_alive(self, process: subprocess.Popen) -> bool:
        """Check if the WSL subprocess is still running."""
        return process is not None and process.poll() is None

    def _drain_output(self) -> list[str]:
        """Drain all available lines from the subprocess stdout queue."""
        lines = []
        while not self._stdout_queue.empty():
            try:
                lines.append(self._stdout_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    @staticmethod
    def _parse_proc_net_tcp_line(line: str) -> dict | None:
        """Parse a single /proc/net/tcp line into a connection dict."""
        line = line.strip()
        if not line or "local_address" in line:
            return None
        parts = line.split()
        if len(parts) < 4:
            return None
        try:
            local_hex, remote_hex, state_hex = parts[1], parts[2], parts[3]
            local_addr_hex, local_port_hex = local_hex.rsplit(":", 1)
            remote_addr_hex, remote_port_hex = remote_hex.rsplit(":", 1)
            local_port = int(local_port_hex, 16)
            remote_port = int(remote_port_hex, 16)
            state = int(state_hex, 16)
            local_addr = socket.inet_ntoa(struct.pack("<I", int(local_addr_hex, 16)))
            remote_addr = socket.inet_ntoa(struct.pack("<I", int(remote_addr_hex, 16)))
            return {
                "state": state,
                "local_addr": local_addr,
                "local_port": local_port,
                "remote_addr": remote_addr,
                "remote_port": remote_port,
            }
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _tcp_state_is_active(state_hex: int) -> bool:
        """Return True only for ESTABLISHED."""
        return state_hex == WslTcpConnectionHandler.ESTABLISHED

    def get_connections(self) -> list[dict]:
        """Get active TCP connections from the subprocess."""
        if not self._subprocess_alive(self._process):
            self._process = self._start_subprocess()
            if self._process is None:
                if not self._warning_issued:
                    self._unavailable = "wsl.exe not available"
                    print(f"[{self._unavailable}]")
                    self._warning_issued = True
                return []

        lines = self._drain_output()
        if not lines and not self._subprocess_alive(self._process):
            self._process = self._start_subprocess()
            return []

        # Validate /proc/net/tcp header on first successful read
        for line in lines:
            stripped = line.strip()
            if stripped and "local_address" in stripped:
                self._header_seen = True
                break
        if lines and not self._header_seen:
            if not self._warning_issued:
                self._unavailable = "/proc/net/tcp missing header"
                print(f"[{self._unavailable}]")
                self._warning_issued = True
            return []

        connections = []
        for line in lines:
            parsed = self._parse_proc_net_tcp_line(line)
            if parsed is None:
                continue
            if self._tcp_state_is_active(parsed["state"]):
                connections.append(parsed)
        return connections


class WslTcpHandler(WslTcpConnectionHandler):
    """Handles WSL TCP connection retrieval via /proc/net/tcp."""

    def __init__(self, config: dict) -> None:
        cmd = f"while true; do cat /proc/net/tcp; sleep {config['polling_interval']}; done"
        super().__init__(config, cmd)
        if not self._wsl_available():
            self._unavailable = "wsl.exe not reachable"
            print(f"[{self._unavailable}]")

    def _wsl_available(self) -> bool:
        """Check if wsl.exe is reachable."""
        return self._run_command("echo ok", check=True) is not None

    def get_connections(self) -> list[dict]:
        """Get active TCP connections from WSL /proc/net/tcp."""
        if self._unavailable:
            return []
        if not self._config["wsl_monitoring"]:
            return []
        conns = super().get_connections()
        for c in conns:
            c["source"] = ConnectionSource.WSL
        return conns


# ── Configuration ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config from config.toml if it exists, otherwise return defaults."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
    user_cfg = {}
    if os.path.isfile(config_path):
        with open(config_path, "rb") as f:
            user_cfg = tomllib.load(f)
    config = {**DEFAULTS, **user_cfg}
    pprint.pprint(config, sort_dicts=False)
    return config


if __name__ == "__main__":
    config = load_config()
    monitor = TcpConnectionMonitor(config)
    monitor.run()

