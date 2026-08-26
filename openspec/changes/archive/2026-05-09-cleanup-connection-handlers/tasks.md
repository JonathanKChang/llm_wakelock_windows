## 1. Define the Protocol and Connection Dict Schema

- [x] 1.1 Add `from typing import Protocol` import
- [x] 1.2 Define `TcpConnectionSource` Protocol with `get_connections() -> list[dict]`
- [x] 1.3 Document the connection dict schema: `state`, `local_addr`, `local_port`, `remote_addr`, `remote_port`, `is_wsl` (no `pid`)

## 2. Extract WindowsTcpHandler Class

- [x] 2.1 Create `WindowsTcpHandler` class with `__init__` that stores monitored ports and SSH port config
- [x] 2.2 Move `get_established_tcp_connections()` logic into `WindowsTcpHandler.get_connections()`
- [x] 2.3 Convert the function to return dicts with `is_wsl=False` and without `pid`
- [x] 2.4 Keep constants (`ES_CONTINUOUS`, `ES_SYSTEM_REQUIRED`, `AF_INET`, etc.) as module-level

## 3. Extract WslTcpHandler Class

- [x] 3.1.0 Change helper script from single `cat /proc/net/tcp` to `while true; do cat /proc/net/tcp; sleep <POLLING_INTERVAL>; done` so the persistent subprocess continuously streams at the polling interval instead of dumping once and exiting.
- [x] 3.1.1 Refactor the rest of the logic to handle a persistent subprocess that outputs at polling interval
- [x] 3.1.2 Create `WslTcpHandler` class with `__init__` that stores monitored ports, SSH port config, and initializes subprocess state
- [x] 3.2 Move all WSL subprocess functions as private methods of the class
- [x] 3.3 Move TCP parsing functions into the class
- [x] 3.4 Convert `get_wsl_tcp_connections()` to `get_connections()` returning dicts with `is_wsl=True` and without `pid`
- [x] 3.5 Handlers expose only `__init__` and `get_connections()` — no additional instance methods

## 4. Merge Shared Logic

- [x] 4.1 Create module-level `is_monitored_active(connections, local_ports, remote_ports)` that works with any connection list
- [x] 4.2 Create module-level `is_ssh_active(connections, ssh_start_times, local_ssh_ports, remote_ssh_ports, min_duration)` using `(local_port, remote_port, remote_addr)` keys (no PID)
- [x] 4.3 Remove the old separate `is_monitored_active` and `is_wsl_monitored_active` functions
- [x] 4.4 Update `is_ssh_active` to not reference `conn["pid"]` in key construction

## 5. Unified Connection Formatting

- [x] 5.1 Create `format_active_connections(connections, show_wsl_label=True)` that formats active connection dicts into log strings
- [x] 5.2 When `show_wsl_label=True`, prefix `is_wsl=True` connections with `[wsl]` and others with `[win]`
- [x] 5.3 When `show_wsl_label=False`, no prefix is added

## 6. Refactor Main Loop

- [x] 6.1 Create `windows_handler = WindowsTcpHandler(...)` at module level
- [x] 6.2 Create `wsl_handler = WslTcpHandler(...)` only when `ENABLE_WSL_MONITORING` is True
- [x] 6.3 Update `has_active_connections()` to use `windows_handler.get_connections()` and `is_monitored_active()` / `is_ssh_active()` with both sources
- [x] 6.4 Update the main loop's logging to use `format_active_connections()` for both Windows and WSL connections
- [x] 6.5 Remove the duplicate `get_wsl_tcp_connections()` call in the logging block (use handler's `get_connections()`)

## 7. Update Tests

- [x] 7.1 Update `_make_conn()` helper in `test_wakelock.py` to omit `pid` and add `is_wsl`
- [x] 7.2 Update `_is_ssh_active()` test helper to use `(local_port, remote_port, remote_addr)` keys
- [x] 7.3 Update `test_ssh_active_after_min_duration`, `test_ssh_not_yet_active`, `test_reconnect_new_pid_resets_timer`, `test_same_pid_reconnect_resets_timer`, `test_non_ssh_port_ignored` to work without PID
- [x] 7.4 Update WSL parsing tests to verify `is_wsl=True` in parsed results
- [x] 7.5 Update `test_is_wsl_monitored_active` to use the new shared function
- [x] 7.6 Add a test for `format_active_connections()` with WSL labels
- [x] 7.7 Run `python test_wakelock.py` to verify all tests pass

## 8. Final Cleanup

- [x] 8.1 Remove unused global variables (`wsl_process`, `wsl_stdout_queue`, etc.) — now encapsulated in `WslTcpHandler`
- [x] 8.2 Verify `config.toml` is unchanged
- [x] 8.3 Verify the script still runs without errors on Windows
- [x] 8.4 Ensure the output format and behavior is identical to before
