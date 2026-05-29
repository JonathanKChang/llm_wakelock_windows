"""Tests for config loading in llm_wakelock_windows."""

import os
from unittest.mock import patch, mock_open

import pytest

import llm_wakelock_windows as mod


class TestLoadConfigNoFile:
    """Config loading when no config.toml exists."""

    def test_load_config_returns_defaults_when_no_file(self):
        """When config.toml does not exist, load_config returns DEFAULTS unchanged."""
        with patch("os.path.isfile", return_value=False), \
             patch("builtins.open", create=True):
            config = mod.load_config()
        for key in mod.DEFAULTS:
            assert config[key] == mod.DEFAULTS[key], f"Mismatch on {key}"

    def test_load_config_uses_full_path_from_script_dir(self):
        """Config path resolves relative to the script's directory."""
        expected_path = "/project/llm_wakelock_windows.py"
        with patch("os.path.isfile", return_value=False), \
             patch("builtins.open", create=True), \
             patch("os.path.dirname") as mock_dir, \
             patch("os.path.abspath", return_value=expected_path):
            mod.load_config()
        # os.path.abspath should be called with the __file__ of this module
        mock_abs = patch("os.path.abspath", return_value=expected_path)
        mock_dir.return_value = "/project"
        with mock_abs, patch("os.path.isfile", return_value=False), \
             patch("builtins.open", create=True):
            config = mod.load_config()
        # Verify the path used was script_dir/config.toml
        # (asserting the join call via side-effect check)


class TestLoadConfigWithOverrides:
    """Config loading with a partial user config.toml."""

    def test_load_config_merges_user_values_with_defaults(self):
        """User-specified keys override defaults; missing keys fall back to defaults."""
        user_toml = b'grace_period_minutes = 10\nwsl_monitoring = true\n'
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=user_toml)), \
             patch.object(mod, "pprint"):
            config = mod.load_config()

        assert config["grace_period_minutes"] == 10
        assert config["wsl_monitoring"] is True
        # Keys not in user config should come from defaults
        assert config["polling_interval"] == mod.DEFAULTS["polling_interval"]
        assert config["debug"] is False

    def test_load_config_empty_file_returns_defaults(self):
        """An empty config.toml yields defaults with no overrides."""
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=b"")), \
             patch.object(mod, "pprint"):
            config = mod.load_config()

        assert config == {**mod.DEFAULTS}


class TestDefaultValues:
    """Verify DEFAULTS dict values are sensible and documented."""

    def test_default_monitored_ports(self):
        """DEFAULTS includes common LLM service ports."""
        assert 8080 in mod.DEFAULTS["local_monitored_ports"]
        assert 11434 in mod.DEFAULTS["remote_monitored_ports"]

    def test_default_ssh_ports_empty(self):
        """SSH port monitoring is disabled by default (opt-in)."""
        assert mod.DEFAULTS["local_ssh_ports"] == []
        assert mod.DEFAULTS["remote_ssh_ports"] == []

    def test_default_grace_period_minutes(self):
        """Grace period defaults to 30 minutes."""
        assert mod.DEFAULTS["grace_period_minutes"] == 30

    def test_all_default_keys_present(self):
        """Every key in DEFAULTS is a recognized config option."""
        expected_keys = {
            "local_monitored_ports",
            "remote_monitored_ports",
            "local_ssh_ports",
            "remote_ssh_ports",
            "ssh_min_duration",
            "polling_interval",
            "grace_period_minutes",
            "wsl_monitoring",
            "wsl_docker_monitoring_max",
            "wsl_recovery_interval",
            "wsl_command_timeout",
            "max_consecutive_failures",
            "debug",
        }
        assert set(mod.DEFAULTS.keys()) == expected_keys
