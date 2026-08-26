## 1. WSL2 Helper Bash Script

- [x] 1.1 Create `wsl2_tcp_monitor.sh` containing a single line: `cat /proc/net/tcp`
- [x] 1.2 No parsing in bash — Python handles all hex parsing of `/proc/net/tcp` output

## 2. Windows-Side Deployment of Helper Script

- [x] 2.1 Add `deploy_wsl2_helper()` function that writes the bash script to `~/bin/wsl2_tcp_monitor.sh` inside WSL2 via `wsl.exe -e bash -c "mkdir -p ~/bin && cat > ..."`
- [x] 2.2 Add `wsl2_helper_available()` function that checks if `wsl.exe` exists and the helper script is present in WSL2
- [x] 2.3 Call `deploy_wsl2_helper()` at startup if `enable_wsl2_monitoring` is true and helper is not yet deployed

## 3. Configuration

- [x] 3.1 Add `enable_wsl2_monitoring` to `DEFAULTS` in config (default: `false`)
- [x] 3.2 Add `enable_wsl2_monitoring` to `config.toml` with commented-out example
- [x] 3.3 Skip all WSL2 logic (subprocess, parsing, monitoring) when `enable_wsl2_monitoring` is `false`

## 4. Persistent Subprocess Manager

- [x] 4.1 Add `_start_wsl2_subprocess()` function that spawns `wsl.exe -e bash ~/bin/wsl2_tcp_monitor.sh` and returns the `subprocess.Popen` object
- [x] 4.2 Add `_wsl2_subprocess_alive()` function that checks if the subprocess is running using `process.poll()`
- [x] 4.3 Add `_drain_wsl2_output()` function that uses `threading` + `queue` to drain available lines without blocking (note: `select.poll()` is Unix-only, replaced with Windows-compatible approach)
- [x] 4.4 Add `_ensure_wsl2_subprocess()` function that starts the subprocess if not running, or restarts it if dead

## 5. TCP Parsing in Python

- [x] 5.1 Add `_parse_proc_net_tcp_line(line)` function that parses a `/proc/net/tcp` line: skips header, splits fields, converts hex local_port and remote_port to decimal, extracts state hex code
- [x] 5.2 Add `_tcp_state_is_active(state_hex)` function that returns True for any state except `07` (CLOSE)
- [x] 5.3 Add `get_wsl2_tcp_connections()` function that drains the subprocess pipe, parses lines, filters active connections, and returns a list of connection dicts matching the Windows connection schema
- [x] 5.4 Handle subprocess death in `get_wsl2_tcp_connections()`: if pipe is empty and process is dead, restart it and return empty list for this cycle

## 6. Integration into Main Monitoring Loop

- [x] 6.1 Add `is_wsl2_monitored_active(connections)` function that checks WSL2 connections against the existing `LOCAL_MONITORED_PORTS` and `REMOTE_MONITORED_PORTS`
- [x] 6.2 Modify `has_active_connections()` to call `get_wsl2_tcp_connections()` and `is_wsl2_monitored_active()` when `enable_wsl2_monitoring` is `true`
- [x] 6.3 Modify `is_ssh_active()` to also accept WSL2 connections and check against `LOCAL_SSH_PORTS` and `REMOTE_SSH_PORTS`
- [x] 6.4 Update log output to include WSL2 connection details when wakelock is acquired due to WSL2 connections
- [x] 6.5 Ensure wakelock is acquired if EITHER Windows OR WSL2 connections are active

## 7. Testing

- [x] 7.1 Add tests for `_parse_proc_net_tcp_line()` with sample `/proc/net/tcp` lines (established, time-wait, close-wait, listen states)
- [x] 7.2 Add tests for `_tcp_state_is_active()` with all state codes
- [x] 7.3 Add tests for `is_wsl2_monitored_active()` with sample WSL2 connection data
- [x] 7.4 Add tests for `deploy_wsl2_helper()` and `wsl2_helper_available()` stubs (test return values, skip actual WSL2 calls)
- [x] 7.5 Verify existing tests still pass after changes
