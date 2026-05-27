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
import datetime
import tomllib
import os
import signal
import pprint
import ctypes
from tcp_handlers import (
    ConnectionSource,
    TcpConnectionSource,
    WindowsTcpHandler,
    WslTcpHandler,
    WslDockerManager,
)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULTS = {
    "local_monitored_ports": [8080, 11434],
    "remote_monitored_ports": [8080, 11434],
    "local_ssh_ports": [],
    "remote_ssh_ports": [],
    "ssh_min_duration": 30.0,
    "polling_interval": 5.0,
    "grace_period_minutes": 30,
    "wsl_monitoring": False,
    "wsl_docker_monitoring_max": 0,
    "wsl_recovery_interval": 60,
    "wsl_command_timeout": 10,
    "max_consecutive_failures": 10,
    "debug": False,
}


# ── Connection dict schema ────────────────────────────────────────────────────
# Returned by TcpConnectionSource.get_connections():
#   state         (int)   — TCP state code (5 = ESTABLISHED)
#   local_addr    (str)   — dotted IPv4 address
#   local_port    (int)   — local port number
#   remote_addr   (str)   — dotted IPv4 address
#   remote_port   (int)   — remote port number
#   source        (ConnectionSource) — WINDOWS, WSL, or WSL_DOCKER
#   container_id  (str)   — Docker container short ID (only for WSL_DOCKER)


# ── Main loop ─────────────────────────────────────────────────────────────────

class TcpConnectionMonitor:
    """Orchestrates connection handlers and manages the wakelock main loop."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._debug = config["debug"]

        self._handlers: list[TcpConnectionSource] = [WindowsTcpHandler(config)]
        if config["wsl_monitoring"]:
            self._handlers.append(WslTcpHandler(config))
        if config["wsl_docker_monitoring_max"] >= 1:
            self._handlers.append(WslDockerManager(config))
        self._ssh_start_times: dict = {}

    @staticmethod
    def is_monitored_active(connections: list[dict], local_ports: list[int], remote_ports: list[int]) -> bool:
        """Check if any connection matches monitored ports (works with any source)."""
        for conn in connections:
            if conn["local_port"] in local_ports:
                return True
            if conn["remote_port"] in remote_ports:
                return True
        return False
    
    def is_relevant(self,conn):
        return (
            conn["local_port"] in self._config["local_monitored_ports"]
            or conn["remote_port"] in self._config["remote_monitored_ports"]
            or conn["local_port"] in self._config["local_ssh_ports"]
            or conn["remote_port"] in self._config["remote_ssh_ports"]
        )

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

    def format_connections(self, connections: list[dict], show_source_label: bool = True) -> list[str]:
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

    def _cleanup_handlers(self) -> None:
        """Clean up all handlers (each handler is responsible for its own resources)."""
        for handler in self._handlers:
            handler.cleanup()

    def get_all_connections(self) -> list[dict]:
        """Collect connections from all handlers into a single list."""
        all_conns: list[dict] = []
        for handler in self._handlers:
            all_conns.extend(handler.get_connections())
        return all_conns

    def has_active_connections(self, connections: list[dict], config: dict) -> bool:
        """Check if the given connections list has any active monitored-port or SSH connections."""
        return self.is_monitored_active(connections, config["local_monitored_ports"], config["remote_monitored_ports"]) or \
               self.is_ssh_active(connections, config["local_ssh_ports"], config["remote_ssh_ports"], config["ssh_min_duration"])

    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001

    def _acquire(self) -> None:
        """Acquires system wake lock to prevent sleep."""
        ctypes.windll.kernel32.SetThreadExecutionState(
            self._ES_CONTINUOUS | self._ES_SYSTEM_REQUIRED
        )

    def _release(self) -> None:
        """Attempts to reset idle timer then releases system wake lock."""
        ctypes.windll.kernel32.SetThreadExecutionState(self._ES_SYSTEM_REQUIRED)
        time.sleep(1)
        ctypes.windll.kernel32.SetThreadExecutionState(self._ES_CONTINUOUS)

    def run(self) -> None:
        """Main loop: monitor connections and manage wakelock."""
        if sys.platform != "win32":
            print("Error: this script requires Windows", file=sys.stderr)
            sys.exit(1)

        # Register signal handlers for clean shutdown (closure captures self)
        def _signal_handler(signum, frame) -> None:
            print("\nShutdown signal received, cleaning up...")
            self._cleanup_handlers()
            sys.exit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        inactive_since: datetime.datetime | None = None
        wakelock = False
        grace_period_seconds = config['grace_period_minutes'] * 60
        polling_interval = self._config["polling_interval"]
        while True:
            loop_start = time.time()
            all_conns = self.get_all_connections()
            active = self.has_active_connections(all_conns, self._config)
            now = datetime.datetime.now()

            if self._debug:
                    print(f"[{now}] [DEBUG]: all connections):\n" + "\n".join(self.format_connections(all_conns)))

            if active:
                relevant_conns = list(filter(self.is_relevant, all_conns))
                if wakelock and inactive_since is not None:
                    print(f"[{now}] Active connections:\n" + "\n".join(self.format_connections(relevant_conns)))
                    inactive_since = None
                elif not wakelock:
                    print(f"[{now}] Acquiring wakelock due to active connections:\n" + "\n".join(self.format_connections(relevant_conns)))
                    self._acquire()
                    wakelock = True

            elif wakelock:
                if inactive_since is None:
                    print(f"[{now}] No more active connections")
                    inactive_since = now

                if (now - inactive_since).total_seconds() >= grace_period_seconds:
                    print(f"[{now}] Releasing wakelock")
                    self._release()
                    wakelock = False
                    inactive_since = None

            elapsed = time.time() - loop_start
            remaining = polling_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
                

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
