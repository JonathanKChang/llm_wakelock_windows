## Why

The current monitor only queries Windows TCP connections via `iphlpapi.GetExtendedTcpTable`, but WSL2 runs in a separate virtualized network stack. Connections to WSL2-hosted services (e.g., an LLM server running inside WSL2) are invisible to Windows APIs, so the wakelock is never acquired even though active work is happening.

## What Changes

- Add a `enable_wsl2_monitoring` config toggle (default: `false`)
- When enabled, the existing port lists (`local_monitored_ports`, `remote_monitored_ports`, `local_ssh_ports`, `remote_ssh_ports`) apply to both Windows and WSL2 connections
- Maintain a persistent subprocess inside WSL2 reading `/proc/net/tcp` and write parsed lines to stdout
- Python does all parsing — the bash helper is just `cat /proc/net/tcp`
- Detect subprocess death and WSL restarts, auto-recover by restarting the subprocess
- When disabled or WSL2 unavailable, skip WSL2 monitoring entirely

## Capabilities

### New Capabilities

- `wsl2-tcp-monitor`: Maintain a persistent WSL2 subprocess reading `/proc/net/tcp`, parse results in Python, and integrate WSL2 connection state into the main monitoring loop using the same port/SSH config as Windows monitoring, with auto-recovery on subprocess death or WSL restart

## Impact

- **Affected code**: `llm_wakelock_windows.py` (main monitoring loop, new subprocess manager), `config.toml` (new `enable_wsl2_monitoring` toggle)
- **No new dependencies**: Uses existing `subprocess` module and `wsl.exe` CLI
- **No admin privileges required**: `wsl.exe` runs under the current user
- **New files**: `wsl2_tcp_monitor.sh` — one-line bash helper deployed to WSL2
