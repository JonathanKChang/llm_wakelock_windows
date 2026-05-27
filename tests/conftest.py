"""Shared pytest fixtures and helpers for the llm_wakelock_windows test suite.

Fixtures:
    _default_config: minimal valid config dict (shared by all test modules)
    _make_monitor: TcpConnectionMonitor instance with default config + injected handlers
    _drain: SubprocessDrain instance with minimal config
    _ssh_conn: helper to build minimal connection dicts for SSH tests

Markers:
    windows: tests requiring Windows OS (auto-skipped on Linux unless explicitly requested)
"""

import sys
import time
from unittest.mock import MagicMock

import pytest

import llm_wakelock_windows as mod
import tcp_handlers


# ── Default config used by all test modules ───────────────────────────────────

def _default_config():
    """Return a minimal valid config dict with SSH ports enabled."""
    return {
        "local_monitored_ports": [8080, 11434],
        "remote_monitored_ports": [8080, 11434],
        "local_ssh_ports": [22],
        "remote_ssh_ports": [22],
        "ssh_min_duration": 30.0,
        "polling_interval": 5.0,
        "grace_period_minutes": 30,
        "wsl_monitoring": False,
        "wsl_docker_monitoring_max": 0,
        "wsl_command_timeout": 10,
        "wsl_recovery_interval": 60,
        "max_consecutive_failures": 3,
        "debug": False,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def default_config():
    """Minimal valid config dict shared by all test modules."""
    return _default_config()


@pytest.fixture()
def make_monitor(default_config):
    """Factory: create a TcpConnectionMonitor with injected mock handlers.

    Usage:
        monitor = make_monitor()                     # empty handler list
        monitor = make_monitor([mock_handler])         # custom handlers
    """
    def _make(config=None, handlers=None):
        cfg = config if config is not None else default_config.copy()
        return mod.TcpConnectionMonitor(cfg, handlers=handlers)
    return _make


@pytest.fixture()
def drain():
    """Create a SubprocessDrain with minimal config.

    Usage:
        d = drain()
        d = drain(max_consecutive_failures=5)   # override via kwargs
    """
    def _drain(config=None, **overrides):
        c = {
            "polling_interval": 1.0,
            "wsl_command_timeout": 10,
            "max_consecutive_failures": 3,
            "wsl_recovery_interval": 60,
        }
        if config:
            c.update(config)
        c.update(overrides)
        return tcp_handlers.SubprocessDrain("echo test", c)
    return _drain


@pytest.fixture()
def ssh_conn():
    """Factory: build a minimal connection dict for SSH tests."""
    def _ssh_conn(local_addr="0.0.0.0", local_port=54321, remote_port=22, remote_addr="10.0.0.1"):
        return {
            "state": 5,
            "local_addr": local_addr,
            "local_port": local_port,
            "remote_addr": remote_addr,
            "remote_port": remote_port,
        }
    return _ssh_conn


# ── Windows marker skip logic ─────────────────────────────────────────────────

def pytest_configure(config):
    """Register the 'windows' marker so pytest does not warn about unknown markers."""
    config.addinivalue_line("markers", "windows: test requires Windows OS")


def pytest_runtest_setup(item):
    """Skip @pytest.mark.windows tests on non-Windows platforms."""
    if item.get_closest_marker("windows"):
        if sys.platform != "win32":
            pytest.skip("requires Windows OS")
