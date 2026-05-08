"""TCP connection handlers for Windows, WSL, and Docker-in-WSL."""
import subprocess
import threading
import queue
import ctypes
import socket
import struct
from enum import Enum
from typing import Protocol


class ConnectionSource(Enum):
    WINDOWS = 0
    WSL = 1
    WSL_DOCKER = 2


class TcpConnectionSource(Protocol):
    """Protocol for TCP connection sources (Windows and WSL)."""

    def get_connections(self) -> list[dict]: ...
    unavailable: bool


# ── Constants ──────────────────────────────────────────────────────────────────

class TcpConnectionMonitor:
    """Named constants shared across handlers and the main loop."""

    ESTABLISHED = 0x01
    MIB_TCP_STATE_ESTAB = 5
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    AF_INET = 2
    TCP_TABLE_OWNER_PID_ALL = 5
    ERROR_INSUFFICIENT_BUFFER = 122


# ── Handlers ───────────────────────────────────────────────────────────────────


class WindowsTcpHandler:
    """Handles Windows TCP connection retrieval via iphlpapi."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self.unavailable: bool = False

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
        self.unavailable: bool = False
        self._command = command
        self._process: subprocess.Popen | None = None
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._header_seen = False
        self._debug = config.get("debug", False)

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
        if self._debug and lines:
            print(f"  [raw] {len(lines)} lines from {self._command[:60]}")
            for line in lines:
                print(f"    {line.rstrip()}")
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
                if not self.unavailable:
                    self.unavailable = True
                    print("[WARN] wsl.exe not available — WSL connections will not be monitored")
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
            if not self.unavailable:
                self.unavailable = True
                print("[WARN] /proc/net/tcp missing header — connections will not be monitored")
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
            self.unavailable = True
            print("[WARN] wsl.exe not reachable — WSL connections will not be monitored")

    def _wsl_available(self) -> bool:
        """Check if wsl.exe is reachable."""
        return self._run_command("echo ok", check=True) is not None

    def get_connections(self) -> list[dict]:
        """Get active TCP connections from WSL /proc/net/tcp."""
        if self.unavailable:
            return []
        if not self._config["wsl_monitoring"]:
            return []
        conns = super().get_connections()
        for c in conns:
            c["source"] = ConnectionSource.WSL
        return conns


class WslDockerTcpHandler(WslTcpConnectionHandler):
    """Handles TCP connections for a single Docker container in WSL."""

    def __init__(self, config: dict, container_id: str) -> None:
        short_id = container_id[:12]
        cmd = f"docker exec {short_id} sh -c \"while true; do cat /proc/net/tcp; sleep {config['polling_interval']}; done\""
        super().__init__(config, cmd)
        self._container_id = short_id
        # Check docker accessibility
        if self._run_command(f"docker exec {short_id} echo ok", check=True) is None:
            self.unavailable = True
            print(f"[WARN] docker container {short_id} not accessible — this container will not be monitored")

    def get_connections(self) -> list[dict]:
        """Get active TCP connections from Docker container."""
        if self.unavailable:
            return []
        conns = super().get_connections()
        for c in conns:
            c["source"] = ConnectionSource.WSL_DOCKER
            c["container_id"] = self._container_id
        return conns


class WslDockerManager(TcpConnectionSource):
    """Manages multiple WslDockerTcpHandler instances with auto-discovery."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self.unavailable: bool = False
        self._container_handlers: list[WslDockerTcpHandler] = []
        self._discover_containers()

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

    def _discover_containers(self) -> None:
        """Discover running Docker containers, cap at wsl_docker_monitoring_max."""
        max_containers = self._config.get("wsl_docker_monitoring_max", 0)
        if max_containers < 1:
            return
        result = self._run_command("docker ps --format '{{.ID}}'", check=True)
        if result is None or result.returncode != 0:
            if not self.unavailable:
                self.unavailable = True
                print("[WARN] docker not available in WSL — Docker connections will not be monitored")
            return
        container_ids = [cid.strip() for cid in result.stdout.strip().split("\n") if cid.strip()]
        for cid in container_ids[:max_containers]:
            handler = WslDockerTcpHandler(self._config, cid)
            if not handler.unavailable:
                self._container_handlers.append(handler)

    def get_connections(self) -> list[dict]:
        """Aggregate connections from all container handlers."""
        if self.unavailable:
            return []
        all_conns: list[dict] = []
        for handler in self._container_handlers:
            all_conns.extend(handler.get_connections())
        return all_conns
