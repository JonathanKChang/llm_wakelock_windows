"""TCP connection handlers for Windows, WSL, and Docker-in-WSL."""
import datetime
import subprocess
import sys
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


class SubprocessDrain:
    """Persistent subprocess that runs a command in a loop, draining stdout into a queue.
    This is done to limit the problemetic calls to wsl.exe, which can fail with any sort of system load.

    Constructs the loop: `echo <sentinel>; while true; do <command> || break; echo <sentinel>; sleep <interval>; done`
    drain() uses queue.get_nowait() to collect any output available, then scans for the last
    two sentinel occurrences, and returns lines between them.

    SubprocessDrain owns its own lifecycle: detects process death, restarts automatically
    with cooldown. No exceptions propagate to callers.
    """

    def __init__(self, command: str, config: dict | None = None,
                 sentinel: str = "__SUBPROCESS_DRAIN__", owner: str = "subprocess") -> None:
        self._process: subprocess.Popen | None = None
        self._queue: queue.Queue[str] = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None
        self._sentinel = sentinel
        self._command = command
        self._stop_timeout = config["wsl_command_timeout"]
        self._interval = config["polling_interval"]
        self._max_consecutive_failures = config["max_consecutive_failures"]
        self._recovery_interval = config["wsl_recovery_interval"]
        self._consecutive_failures = 0
        self._stopped = False
        self._last_output: list[str] | None = None
        self._full_command = f"echo {sentinel}; while true; do {command} || break; echo {sentinel}; sleep {self._interval}; done"
        self._owner = owner
        self._last_restart_attempt: float = 0.0
        self._death_warned: bool = False

    @staticmethod
    def _wsl_running() -> bool:
        """Check if any WSL distro is currently running by listing Windows processes.
        """
        if sys.platform != "win32": # for tests
            return True
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq wsl.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return "wsl.exe" in result.stdout
        except Exception:
            return False

    def start(self) -> subprocess.Popen | None:
        """Spawn the persistent subprocess and start the drain thread.
        """
        if not self._wsl_running():
            return None
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
            time.sleep(0.5)  # let subprocess produce initial output
            return proc
        except (FileNotFoundError, OSError, AttributeError):
            return None

    @staticmethod
    def _drain_loop(process: subprocess.Popen, queue: queue.Queue[str]) -> None:
        """Daemon thread: read stdout lines into queue."""
        try:
            for line in process.stdout:
                queue.put(line)
        except Exception:
            pass

    @staticmethod
    def _find_last_sentinel_pair(lines: list[str], sentinel: str) -> tuple[int, int] | None:
        """Scan lines for the last two sentinel occurrences.

        Returns (first_idx, second_idx) or None if fewer than 2 sentinels found.
        """
        hits: list[int] = []

        for i, line in enumerate(lines):
            if sentinel in line:
                hits.append(i)

        if len(hits) < 2:
            return None

        return hits[-2], hits[-1]

    def drain(self) -> list[str]:
        """Drain lines between the last two sentinel occurrences.

        Non-blocking: gets only lines currently in the queue. Drains all
        available lines, checks for the last sentinel pair, and returns
        lines between them. Detects process death and handles restart.
        No exceptions propagate to callers.
        """
        if not self._wsl_running():
            self._restart_if_needed()
            return []

        if self._process is None:
            self.start()
        elif self._process.poll() is not None:
            # Process death — tracked separately from sentinel misses
            if not self._death_warned:
                print(f"[{datetime.datetime.now().isoformat()}] [WARN] {self._owner} subprocess died")
                self._death_warned = True
            self._restart_if_needed()
            return self._last_output if self._last_output is not None else []

        # Get only currently available lines (non-blocking)
        try:
            line = self._queue.get_nowait()
            all_lines = [line]
        except queue.Empty:
            all_lines = []

        pair = self._find_last_sentinel_pair(all_lines, self._sentinel)
        if pair is not None:
            if self._death_warned:
                print(f"[{datetime.datetime.now().isoformat()}] [INFO] {self._owner} re-established")
                self._death_warned = False
            self._consecutive_failures = 0
            result = all_lines[pair[0] + 1:pair[1]]
            # Put back the second sentinel and any lines after it
            for line in all_lines[pair[1]:]:
                self._queue.put(line)
            self._last_output = result
            return result

        # No pair found — subprocess loop broke or is slow. Count as failure.
        for line in all_lines:
            self._queue.put(line)

        self._consecutive_failures += 1
        # Warn once at halfway, restart at threshold (cooldown-gated)
        if self._max_consecutive_failures > 2 and self._consecutive_failures == (self._max_consecutive_failures + 1) // 2:
            print(
                f"[{datetime.datetime.now().isoformat()}] [WARN] {self._owner} "
                f"missed {self._consecutive_failures}/{self._max_consecutive_failures} sentinels in a row"
            )
        self._restart_if_needed()

        return self._last_output if self._last_output is not None else []

    def restart(self) -> None:
        """Stop the current subprocess, sleep briefly, then start a new one."""
        self.stop()
        time.sleep(0.5)
        self.start()
        self._consecutive_failures = 0
        self._stopped = False

    def _restart_if_needed(self) -> None:
        """Restart subprocess if failure threshold is met and cooldown has elapsed.

        Checks max_consecutive_failures threshold and wsl_recovery_interval cooldown.
        Only triggers once per cooldown period.

        Note: does NOT print success here — the process may spawn but fail to produce
        output until WSL/Docker are fully ready. Success is logged in drain() when
        we first receive a valid sentinel pair after restart.
        """
        if (time.time() - self._last_restart_attempt) < self._recovery_interval:
            return  # cooldown not elapsed

        if self._consecutive_failures >= self._max_consecutive_failures:
            print(
                f"[{datetime.datetime.now().isoformat()}] [WARN] {self._owner} "
                f"restarting after {self._consecutive_failures} missed sentinels"
            )
            self._last_restart_attempt = time.time()
            self.restart()

    def stop(self) -> None:
        """Terminate the subprocess and wait for the drain thread."""
        self._stopped = True
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=self._stop_timeout)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                try:
                    self._process.kill()
                    self._process.wait(timeout=self._stop_timeout)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._stop_timeout)


class TcpConnectionSource(Protocol):
    """Protocol for TCP connection sources (Windows and WSL)."""

    def get_connections(self) -> list[dict]: ...
    def cleanup(self) -> None: ...
    _stopped: bool


# ── Handlers ───────────────────────────────────────────────────────────────────


class WindowsTcpHandler(TcpConnectionSource):
    """Handles Windows TCP connection retrieval via iphlpapi."""

    AF_INET = 2
    TCP_TABLE_OWNER_PID_ALL = 5
    MIB_TCP_STATE_ESTAB = 5
    ERROR_INSUFFICIENT_BUFFER = 122

    def __init__(self, config: dict) -> None:
        self._config = config
        self._stopped = False
        self._debug = config["debug"]

    def cleanup(self) -> None:
        """No-op - Windows handler uses iphlpapi, no subprocesses to clean up."""
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


class WslTcpConnectionHandler(TcpConnectionSource):
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
        self._stopped = False
        self._drain = SubprocessDrain(command, config, owner="WSL monitoring")
        self._debug = config["debug"]
        if self._drain.start() is None:
            print(f"[WARN] WSL is not accessible - recovery attempts will be made automatically")

    def cleanup(self) -> None:
        """Terminate the WSL subprocess and its child process tree."""
        if self._stopped:
            return
        self._stopped = True
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
        if self._stopped:
            return []

        lines = self._drain.drain()

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
        cmd = "cat /proc/net/tcp"
        super().__init__(config, cmd)
        print(f"[{datetime.datetime.now().isoformat()}] [INFO] WSL monitoring started")

    def get_connections(self) -> list[dict]:
        """Get active TCP connections from WSL /proc/net/tcp."""
        if self._stopped:
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
        cmd = f"docker exec {short_id} sh -c 'cat /proc/net/tcp'"
        self._config = config
        self._stopped = False
        self._drain = SubprocessDrain(cmd, config, owner=f"Docker container {short_id} monitoring")
        self._debug = config["debug"]
        self._container_id = short_id
        print(f"[{datetime.datetime.now().isoformat()}] [INFO] WSL-Docker {short_id} monitoring started")

    def get_connections(self) -> list[dict]:
        """Get active TCP connections from Docker container."""
        if self._stopped:
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
        self._stopped = False
        self._last_discovery_time: float = 0.0
        self._handlers: dict[str, WslDockerTcpHandler] = {}
        self._drain = SubprocessDrain("docker ps --format '{{.ID}}'", config, owner="WSL-Docker lifecycle monitoring")
        self._drain.start()
        self._discover()
        print(f"[{datetime.datetime.now().isoformat()}] [INFO] WSL-Docker lifecycle monitoring started")

    def _discover(self) -> None:
        """Drain docker ps output, then diff against _handlers."""
        if self._stopped:
            return

        lines = self._drain.drain()
        
        if not lines:
            self._last_discovery_time = time.time()
            return  # no output yet, skip discovery this cycle
        
        max_containers = self._config["wsl_docker_monitoring_max"]
        if max_containers < 1:
            return
        
        current_ids = [line.strip() for line in lines if line.strip()]
        # Remove stopped containers
        for cid in list(self._handlers):
            if cid not in current_ids:
                short_id = self._handlers[cid]._container_id
                print(f"[{datetime.datetime.now().isoformat()}] [INFO] Docker container {short_id} has exited, stopping monitoring")
                self._handlers[cid].cleanup()
                del self._handlers[cid]

        # Add new containers (up to remaining cap)
        remaining = max_containers - len(self._handlers)
        for cid in current_ids:
            if cid not in self._handlers and remaining > 0:
                handler = WslDockerTcpHandler(self._config, cid)
                if not handler._stopped:
                    self._handlers[cid] = handler
                    remaining -= 1

        self._last_discovery_time = time.time()

    def get_connections(self) -> list[dict]:
        """Aggregate connections from all container handlers."""
        if self._stopped:
            return []
        # Timer-based discovery
        if self._config["wsl_recovery_interval"] > 0 and (self._last_discovery_time == 0 or
                time.time() - self._last_discovery_time >= self._config["wsl_recovery_interval"]):
            self._discover()
        all_conns: list[dict] = []
        for handler in self._handlers.values():
            all_conns.extend(handler.get_connections())
        return all_conns

    def cleanup(self) -> None:
        """Clean up all container handler subprocesses and the discovery process."""
        for handler in self._handlers.values():
            handler.cleanup()
        self._drain.stop()
