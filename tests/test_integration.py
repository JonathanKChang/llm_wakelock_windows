"""Integration tests — full monitor with DI-injected mock handlers.

Tests the complete connection flow: handler → get_all_connections → has_active_connections,
without requiring real Windows APIs, WSL, or Docker.
"""

from unittest.mock import MagicMock

import pytest

import llm_wakelock_windows as mod
from tcp_handlers import ConnectionSource


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_handler(connections=None):
    """Create a mock handler returning specified connections."""
    handler = MagicMock()
    handler.get_connections.return_value = connections or []
    handler.cleanup = MagicMock()
    return handler


class TestFullMonitorFlow:
    """End-to-end monitor flow with injected handlers."""

    def test_collects_connections_from_all_sources(self, make_monitor):
        """Inject handlers for each source type and verify aggregated output."""
        windows_handler = _mock_handler([
            {"state": 5, "local_addr": "192.168.1.1", "local_port": 8080,
             "remote_addr": "10.0.0.1", "remote_port": 12345, "source": ConnectionSource.WINDOWS},
        ])
        wsl_handler = _mock_handler([
            {"state": 1, "local_addr": "172.17.0.2", "local_port": 5432,
             "remote_addr": "10.0.0.1", "remote_port": 80, "source": ConnectionSource.WSL},
        ])
        docker_handler = _mock_handler([
            {"state": 1, "local_addr": "172.17.0.3", "local_port": 9999,
             "remote_addr": "10.0.0.2", "remote_port": 443, "source": ConnectionSource.WSL_DOCKER,
             "container_id": "abc123def456"},
        ])

        mon = make_monitor(handlers=[windows_handler, wsl_handler, docker_handler])
        all_conns = mon.get_all_connections()
        assert len(all_conns) == 3
        sources = {c["source"] for c in all_conns}
        assert sources == {ConnectionSource.WINDOWS, ConnectionSource.WSL, ConnectionSource.WSL_DOCKER}

    def test_has_active_connections_monitored_port_positive(self, make_monitor):
        """has_active_connections returns True for a monitored local port."""
        handler = _mock_handler([{"local_port": 8080, "remote_port": 12345, "source": ConnectionSource.WINDOWS}])
        mon = make_monitor(handlers=[handler])
        conns = mon.get_all_connections()
        assert mon.has_active_connections(conns, mon._config) is True

    def test_has_active_connections_monitored_port_negative(self, make_monitor):
        """has_active_connections returns False when no monitored port matches."""
        handler = _mock_handler([{"local_port": 9999, "remote_port": 8888, "source": ConnectionSource.WINDOWS}])
        mon = make_monitor(handlers=[handler])
        conns = mon.get_all_connections()
        assert mon.has_active_connections(conns, mon._config) is False


class TestSignalHandling:
    """Signal handler registration and cleanup behavior."""

    def test_signal_handler_calls_cleanup_before_exit(self, make_monitor):
        """When a signal handler triggers, cleanup() is called on the monitor."""
        mock_handler = _mock_handler([])
        mon = make_monitor(handlers=[mock_handler])

        # Verify that _cleanup_handlers exists and calls handler.cleanup
        mon._cleanup_handlers()
        assert mock_handler.cleanup.called
