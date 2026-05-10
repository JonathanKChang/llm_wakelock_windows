"""TCP connection handlers for Windows, WSL, and Docker-in-WSL."""
import subprocess
import threading
import time
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


DEFAULT_SENTINEL = "__SUBPROCESS_DRAIN__"


class SubprocessDrain:
    """Persistent subprocess that runs a command in a loop, draining stdout into a queue.

    Constructs the loop: `while true; do echo <sentinel>; <command>; sleep <interval>; done`
    drain() always waits up to `timeout` for the first line, then drains the rest.
    Returns only lines after the most recent sentinel, discarding stale data.
    """

    def __init__(self, command: str, interval: float = 5.0, sentinel: str = DEFAULT_SENTINEL,
                 max_queue_lines: int = 1000, debug_callback=None) -> None:
        self._process: subprocess.Popen | None = None
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max_queue_lines)
        self._thread: threading.Thread | None = None
        self._sentinel = sentinel
        self._debug_callback = debug_callback
        self._full_command = f"while true; do echo {sentinel}; {command}; sleep {interval}; done"

    def start(self) -> subprocess.Popen | None:
        """Spawn the persistent subprocess and start the drain thread."""
        try:
            proc = subprocess.Popen(
                ["wsl.exe", "-e", "sh", "-c", self._full_command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._process = proc
            self._thread = threading.Thread(
                target=self._drain_loop, args=(proc, self._queue), daemon=True
            )
            self._thread.start()
            return proc
        except (FileNotFoundError, OSError):
            return None

    @staticmethod
    def _drain_loop(process: subprocess.Popen, queue: queue.Queue[str]) -> None:
        """Daemon thread: read stdout lines into queue."""
        try:
            for line in process.stdout:
                queue.put(line)
        except Exception:
            pass

    def drain(self, timeout: float = 5.0) -> list[str]:
        """Drain lines since the most recent sentinel.

        Always waits up to `timeout` for the first line, then drains the rest.
        """
        lines: list[str] = []
        try:
            lines.append(self._queue.get(timeout=timeout))
        except queue.Empty:
            return []
        while not self._queue.empty():
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                break
        # Find last sentinel; return only lines after it
        last_idx = None
        for i, line in enumerate(lines):
            if self._sentinel in line:
                last_idx = i
        result = lines[last_idx + 1:] if last_idx is not None else lines
        if self._debug_callback and result:
            self._debug_callback(self._full_command, len(result))
        return result

    @property
    def alive(self) -> bool:
        """True if the subprocess is still running."""
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        """Terminate the subprocess and wait for the drain thread."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)


class TcpConnectionSource(Protocol):
    """Protocol for TCP connection sources (Windows and WSL)."""

    def get_connections(self) -> list[dict]: ...
    def cleanup(self) -> None: ...
    unavailable: bool


# ── Handlers ───────────────────────────────────────────────────────────────────


class WindowsTcpHandler:
    """Handles Windows TCP connection retrieval via iphlpapi."""

    AF_INET = 2
    TCP_TABLE_OWNER_PID_ALL = 5
    MIB_TCP_STATE_ESTAB = 5
    ERROR_INSUFFICIENT_BUFFER = 122

    def __init__(self, config: dict) -> None:
        self._config = config
        self.unavailable: bool = False
        self._debug = config.get("debug", False)

    def cleanup(self) -> None:
        """No-op — Windows handler uses iphlpapi, no subprocesses to clean up."""
        pass

    def get_connections(self) -> list[dict]:
        """Retrieve all established TCP connections from Windows iphlpapi."""
        iphlpapi = ctypes.windll.iphlpapi
        size = ctypes.c_ulong(0)
        ret = iphlpapi.GetExtendedTcpTable(
            None, ctypes.byref(size), True, WindowsTcpHandler.AF_INET, WindowsTcpHandler.TCP_TABLE_OWNER_PID_ALL, 0
        )
        if ret != WindowsTcpHandler.ERROR_INSUFFICIENT_BUFFER:
            raise OSError(f"Unexpected error querying TCP table size: {ret}")

        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedTcpTable(
            buf, ctypes.byref(size), True, WindowsTcpHandler.AF_INET, WindowsTcpHandler.TCP_TABLE_OWNER_PID_ALL, 0
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
            if not self._debug and row.dwState != WindowsTcpHandler.MIB_TCP_STATE_ESTAB:
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
        self._drain = SubprocessDrain(
            command,
            interval=config["polling_interval"],
            sentinel="/proc/net/tcp",
        )
        self._debug = config.get("debug", False)
        self._terminated = False
        self._timeout = config.get("wsl_command_timeout", 10)

    def cleanup(self) -> None:
        """Terminate the WSL subprocess and its child process tree."""
        if self._terminated:
            return
        self._terminated = True
        self._drain.stop()

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
        if self._terminated:
            return []
        if not self._drain.alive:
            self._drain.start()
            if not self._drain.alive:
                if not self.unavailable:
                    self.unavailable = True
                    print("[WARN] wsl.exe not available — WSL connections will not be monitored")
                return []

        lines = self._drain.drain(timeout=self._timeout)
        if not lines:
            if not self._drain.alive:
                self._drain.start()
            return []

        if self._debug and lines:
            print(f"  [DEBUG] {len(lines)} lines from {self._drain._full_command[:60]} ...")
            for line in lines:
                print(f"    {line.rstrip()}")

        connections = []
        for line in lines:
            parsed = self._parse_proc_net_tcp_line(line)
            if parsed is None:
                continue
            if not self._debug and not self._tcp_state_is_active(parsed["state"]):
                continue
            connections.append(parsed)
        return connections


class WslTcpHandler(WslTcpConnectionHandler):
    """Handles WSL TCP connection retrieval via /proc/net/tcp."""

    def __init__(self, config: dict) -> None:
        cmd = f"while true; do cat /proc/net/tcp; sleep {config['polling_interval']}; done"
        super().__init__(config, cmd)
        print("[INFO] WSL monitoring started")

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
        # Check docker accessibility by trying to start the subprocess
        if self._drain.start() is None:
            self.unavailable = True
            print(f"[WARN] docker container {short_id} not accessible — this container will not be monitored")
        else:
            print(f"[INFO] WSL-Docker {short_id} monitoring started")

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
    """Manages multiple WslDockerTcpHandler instances with persistent discovery process."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self.unavailable: bool = False
        self._timeout = config.get("wsl_command_timeout", 10)
        self._discovery_interval = config.get("wsl_docker_discovery_interval", 10)
        self._last_discovery_time: float = 0.0
        self._handlers: dict[str, WslDockerTcpHandler] = {}
        self._discover_drain = SubprocessDrain(
            f"docker ps --format '{{{{.ID}}}}'",
            interval=self._discovery_interval,
            max_queue_lines=1000,
        )
        self._discover_drain.start()
        # Initial discovery — drain() waits for output, no race condition
        initial_lines = self._discover_drain.drain(timeout=self._timeout)
        if initial_lines:
            self._discover(initial_lines)

    def _discover(self, lines: list[str]) -> None:
        """Parse docker ps output, diff against _handlers, add/remove containers, enforce max cap.

        Pre-filters new containers by max cap: keeps existing, discards new.
        """
        max_containers = self._config.get("wsl_docker_monitoring_max", 0)
        if max_containers < 1:
            return
        current_ids = [line.strip() for line in lines if line.strip()]
        # Remove stopped containers
        for cid in list(self._handlers):
            if cid not in current_ids:
                self._handlers[cid].cleanup()
                del self._handlers[cid]
        # Add new containers (up to remaining cap)
        remaining = max_containers - len(self._handlers)
        for cid in current_ids:
            if cid not in self._handlers and remaining > 0:
                handler = WslDockerTcpHandler(self._config, cid)
                if not handler.unavailable:
                    self._handlers[cid] = handler
                    remaining -= 1

    def get_connections(self) -> list[dict]:
        """Aggregate connections from all container handlers."""
        if self.unavailable:
            return []
        # Timer-based discovery
        if self._discovery_interval > 0 and (self._last_discovery_time == 0 or
                time.time() - self._last_discovery_time >= self._discovery_interval):
            lines = self._discover_drain.drain(timeout=self._timeout)
            if lines:
                self._discover(lines)
                self._last_discovery_time = time.time()
        all_conns: list[dict] = []
        for handler in self._handlers.values():
            all_conns.extend(handler.get_connections())
        return all_conns

    def cleanup(self) -> None:
        """Clean up all container handler subprocesses and the discovery process."""
        for handler in self._handlers.values():
            handler.cleanup()
        self._discover_drain.stop()
