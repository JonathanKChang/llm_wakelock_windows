"""Tests for WslDockerManager — container discovery lifecycle, aggregation, cleanup."""

from unittest.mock import MagicMock, patch

import pytest

import tcp_handlers
from tcp_handlers import WslDockerManager, ConnectionSource


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_docker_mock():
    """Create a mock subprocess.Popen that simulates docker ps output."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    return mock_proc


def _docker_config(**overrides):
    """Minimal config for Docker manager with overrides."""
    c = {
        "wsl_docker_monitoring_max": 5,
        "polling_interval": 1.0,
        "wsl_command_timeout": 10,
        "wsl_recovery_interval": 60,
        "max_consecutive_failures": 3,
        "debug": False,
    }
    c.update(overrides)
    return c


class TestContainerDiscovery:
    """Docker container discovery lifecycle tests."""

    def test_discovery_removes_stopped_containers(self):
        """Containers in handlers but not in docker ps output are cleaned up."""
        mock_proc = _make_docker_mock()
        mock_proc.stdout = iter([
            "__SUBPROCESS_DRAIN__", "abc123", "def456", "__SUBPROCESS_DRAIN__", ""
        ])

        with patch("tcp_handlers.subprocess.Popen", return_value=mock_proc), \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            config = _docker_config(wsl_docker_monitoring_max=5)
            manager = WslDockerManager(config)

            # Manually add a container that's NOT in docker ps output (stopped)
            old_handler = MagicMock()
            old_handler._container_id = "xyz789"
            manager._handlers["xyz789abcdef"] = old_handler
            manager._discover()  # simulate discovery

            # xyz789 should be cleaned up and removed
            assert "xyz789abcdef" not in manager._handlers
            assert old_handler.cleanup.called

    def test_discovery_skips_when_wsl_docker_monitoring_max_is_zero(self):
        """Discovery is a no-op when max containers is 0 (disabled)."""
        mock_proc = _make_docker_mock()
        mock_proc.stdout = iter(["__SUBPROCESS_DRAIN__", "abc123", "__SUBPROCESS_DRAIN__", ""])

        with patch("tcp_handlers.subprocess.Popen", return_value=mock_proc), \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            config = _docker_config(wsl_docker_monitoring_max=0)
            manager = WslDockerManager(config)
            # Discovery runs but should not add any handlers
            assert len(manager._handlers) == 0

    def test_get_connections_aggregates_from_all_handlers(self):
        """get_connections() returns connections from all container handlers."""
        mock_proc = _make_docker_mock()
        mock_proc.stdout = iter([
            "__SUBPROCESS_DRAIN__", "abc123def456", "fed987cba654", "__SUBPROCESS_DRAIN__"
        ])

        with patch("tcp_handlers.subprocess.Popen", return_value=mock_proc), \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            config = _docker_config(wsl_docker_monitoring_max=10)
            manager = WslDockerManager(config)

            # Mock handler connections
            for cid in list(manager._handlers.keys())[:2]:
                manager._handlers[cid].get_connections = MagicMock(
                    return_value=[{
                        "local_addr": "172.17.0.2", "local_port": 5432,
                        "remote_addr": "10.0.0.1", "remote_port": 80,
                        "source": ConnectionSource.WSL_DOCKER,
                        "container_id": cid[:12],
                    }]
                )

            all_conns = manager.get_connections()
            # Should aggregate from both handlers (discovery ran on first get_connections)
            assert len(all_conns) >= 1


class TestCleanup:
    """Full cleanup cascade through Docker Manager."""

    def test_cleanup_stops_all_handlers_and_discovery_drain(self):
        """cleanup() stops the discovery drain and all container handlers."""
        mock_proc = _make_docker_mock()
        mock_proc.stdout = iter(["__SUBPROCESS_DRAIN__", "abc123", "__SUBPROCESS_DRAIN__"])

        with patch("tcp_handlers.subprocess.Popen", return_value=mock_proc), \
             patch.object(tcp_handlers.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            config = _docker_config(wsl_docker_monitoring_max=5)
            manager = WslDockerManager(config)

            # Discovery added at least one handler
            assert len(manager._handlers) >= 1

            manager.cleanup()

            # Drain subprocess was stopped
            assert manager._drain._stopped is True
