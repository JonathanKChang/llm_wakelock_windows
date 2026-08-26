## Why

When WSL or Docker shuts down (laptop sleep, `wsl --shutdown`, Docker Desktop restart), all monitoring subprocesses die and the handlers permanently stop — the entire monitoring stops until the script is manually restarted. This causes silent loss of connection tracking for 5-30+ seconds, during which active monitored connections may be missed and the wakelock incorrectly released.

## What Changes

- **SubprocessDrain auto-restarts** when the subprocess dies or fails to produce data for `max_consecutive_failures` iterations, using a cooldown interval derived from config
- **SubprocessDrain owns its lifecycle**: detects process death via `poll()`, logs warnings with timestamps and owner names, resets failure counters on successful restart
- **New config parameter** `wsl_recovery_interval` (default 60s) replaces `wsl_docker_discovery_interval`, used for both restart cooldown and Docker discovery cadence
- **Handlers simplified**: `WslTcpConnectionHandler` and `WslDockerManager` no longer manage recovery — they just call `drain()` and parse results
- **Owner string injection** into SubprocessDrain for contextual logging (e.g., "WSL /proc/net/tcp", "Docker container abc123")
- **Remove `SentinelNotFound` exception**: no longer raised; subprocess failures handled internally by auto-restart

## Capabilities

### Modified Capabilities

- `synchronous-drain`: subprocess drain behavior changes — now auto-restarts instead of raising an exception on sentinel failure threshold
- `connection-handlers`: handler recovery semantics change — handlers no longer permanently stop on subprocess death
- `wsl2-tcp-monitor`: WSL/Docker monitoring gains automatic recovery from subprocess death; discovery and container handlers recover transparently

## Impact

- **Files**: `tcp_handlers.py`, `llm_wakelock_windows.py`
- **Behavior**: SubprocessDrain may now restart the subprocess during operation (previously it would permanently stop). This changes the failure semantics — no more exceptions, just automatic recovery with logged warnings.
- **Config**: `wsl_docker_discovery_interval` renamed to `wsl_recovery_interval`
- **Removal**: `SentinelNotFound` exception class removed
