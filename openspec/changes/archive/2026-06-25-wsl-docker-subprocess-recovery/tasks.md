## 1. SubprocessDrain core changes

- [x] 1.1 Add `owner` parameter to `SubprocessDrain.__init__()`, store as `self._owner = owner` (default "subprocess")
- [x] 1.2 Add `_config`, `_last_restart_attempt`, `_death_warned` fields to `__init__`
- [x] 1.3 Implement `restart()` method: calls `stop()`, sleeps 0.5s, calls `start()`, resets `_consecutive_failures=0` on success
- [x] 1.4 Implement `_restart_if_needed(config)` method: checks `max_consecutive_failures` threshold and `wsl_recovery_interval` cooldown
- [x] 1.5 Modify `drain()` to detect process death via `_process.poll() != None`, log warning with timestamp/owner, call `_restart_if_needed()`
- [x] 1.6 Modify `drain()` sentinel miss path: remove `raise SentinelNotFound`, replace with return cached or `[]`
- [x] 1.7 Add logging on successful restart: `[INFO] <owner> restarted successfully`
- [x] 1.8 Add "re-established" logging when fresh data appears after `_death_warned` was True

## 2. Handler simplification

- [x] 2.1 Remove `except SentinelNotFound` block from `WslTcpConnectionHandler.get_connections()`
- [x] 2.2 In `WslTcpConnectionHandler.__init__()`, don't set `_stopped = True` when `start()` returns None
- [x] 2.3 Pass `owner` string to `SubprocessDrain` in `WslTcpConnectionHandler.__init__()` (e.g., "WSL /proc/net/tcp")
- [x] 2.4 Pass `owner` string to `SubprocessDrain` in `WslDockerManager.__init__()` (e.g., "WSL-Docker docker ps discovery")
- [x] 2.5 Verify `WslDockerTcpHandler` inherits simplified handler behavior (no extra recovery logic needed)

## 3. Config and cleanup

- [x] 3.1 Rename `wsl_docker_discovery_interval` to `wsl_recovery_interval` in `llm_wakelock_windows.py` DEFAULTS (keep default 60)
- [x] 3.2 Update `WslDockerManager` to reference `config["wsl_recovery_interval"]` instead of `config["wsl_docker_discovery_interval"]`
- [x] 3.3 Remove `SentinelNotFound` exception class definition from `tcp_handlers.py`
- [x] 3.4 Remove `from tcp_handlers import SentinelNotFound` (or its usage) in `llm_wakelock_windows.py` if present

## 4. Verification

- [x] 4.1 Verify SubprocessDrain restart flow: dead subprocess → detection → restart → fresh data
- [x] 4.2 Verify cooldown prevents rapid restart attempts (< 60s between attempts)
- [x] 4.3 Verify logging messages have correct timestamps and owner names
- [x] 4.4 Verify WslDockerManager discovery still works with renamed config key
- [x] 4.5 Verify handlers no longer permanently stop on subprocess death

> Note: Tasks completed via Ralph loop (`.ralph/wsl-docker-subprocess-recovery.md`), then archived.
