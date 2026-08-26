## Context

The current `llm_wakelock_windows.py` monitors TCP connections using Windows-only APIs (`iphlpapi.GetExtendedTcpTable`). WSL2 runs in a separate lightweight utility VM with its own network namespace. TCP connections to WSL2 services (e.g., an LLM server on port 8080 inside WSL2) are invisible to Windows APIs.

WSL2 exposes TCP connection state via `/proc/net/tcp`, a kernel file with a simple text format:
```
sl  local_address:port  remote_address:port  state  ...
0:  00000000:1F90  0500000A:1F40  0A  ...
```
Fields are hex-encoded. State `0A` = ESTABLISHED. This is the authoritative source inside WSL2.

Constraints:
- No admin privileges on Windows
- No new Windows Python packages
- Low CPU overhead
- Minimally affected by WSL restarts
- Low to moderate implementation complexity
- Optional PID correlation

## Goals / Non-Goals

**Goals:**
- Add `enable_wsl2_monitoring` config toggle (default: `false`)
- When enabled, apply existing port/SSH config to both Windows and WSL2 connections — no duplicate config
- Maintain a persistent subprocess inside WSL2 reading `/proc/net/tcp` and write one line per connection to stdout
- Python parses hex-encoded fields into connection dicts
- Detect subprocess death (graceful exit, crash, WSL restart) and auto-restart
- Gracefully handle WSL2 unavailability (not installed, not running)

**Non-Goals:**
- PID correlation between Windows and WSL2 (optional, out of scope for v1)
- Monitoring UDP connections (TCP only, `/proc/net/tcp`)
- Real-time event-driven monitoring (polling-based, same as existing)
- Cross-platform support (Windows-only, as per existing)
- Separate WSL2 port list — reuse existing config

## Decisions

### Decision 1: Single config toggle, shared port lists
**Chosen:** Add `enable_wsl2_monitoring: true/false` to config. When `true`, the existing `local_monitored_ports`, `remote_monitored_ports`, `local_ssh_ports`, and `remote_ssh_ports` apply to both Windows and WSL2 connections. When `false` or WSL2 unavailable, only Windows connections are monitored.

**Rationale:** No need for a duplicate `wsl2_monitored_ports` list. The user wants the same ports monitored regardless of whether the service runs on Windows or WSL2. A single toggle keeps config simple.

**Alternatives considered:**
- *Separate WSL2 port list:* Adds config complexity and duplication. Rejected — same ports should apply to both.

### Decision 2: Persistent subprocess reading `/proc/net/tcp`
**Chosen:** Spawn `wsl.exe -e bash ~/bin/wsl2_tcp_monitor.sh` once at startup, keep the process alive, read lines from stdout as they arrive. Check `poll()` on the stdout pipe to avoid blocking. On each poll cycle, drain available lines, then check if the process is still alive. If dead, restart.

**Rationale:** `/proc/net/tcp` is a live file — new connections produce new lines. A persistent `cat` process is essentially zero overhead. Python drains lines from the pipe on each poll cycle. This avoids per-poll `wsl.exe` startup cost (~50-100ms) and gives near-real-time detection.

**Alternatives considered:**
- *Per-poll `wsl.exe` spawn:* Simple but adds ~50-100ms overhead per 5s poll. Rejected in favor of persistent subprocess.

### Decision 3: Bash script is just `cat /proc/net/tcp`
**Chosen:** The bash helper is a single command: `cat /proc/net/tcp`. Python does all hex parsing.

**Rationale:** Minimal bash logic reduces fragility. `cat` is a built-in that never fails (unless the file disappears, which signals WSL restart). All parsing logic stays in Python where it's testable and maintainable.

**Format:** Each line (after the header) has the format:
```
<sl> <local_addr>:<local_port> <remote_addr>:<remote_port> <state_hex> ...
```
All addresses and ports are 8-char and 4-char hex respectively.

### Decision 4: Subprocess death detection via pipe EOF + poll
**Chosen:** On each poll cycle: (1) use `select.poll()` on the subprocess stdout to drain available lines, (2) check `process.poll()` to detect if the process exited, (3) if dead, restart it.

**Rationale:** `select.poll()` is non-blocking and available on Windows via `subprocess` stdout. Checking `process.poll()` after draining detects both graceful exit and crashes. WSL restarts cause the pipe to EOF, which `poll()` + `poll()` detects.

### Decision 5: Parse `/proc/net/tcp` state codes in Python
**Chosen:** Map hex state codes to a set of "active" states: `{02 (SYN_SENT), 03 (SYN_RECV), 01 (ESTABLISHED), 04 (FIN_WAIT1), 05 (FIN_WAIT2), 06 (TIME_WAIT), 07 (CLOSE), 08 (CLOSE_WAIT), 09 (LAST_ACK), 0A (LISTEN)}`. Any state other than `01` (CLOSE) indicates an active connection.

**Rationale:** Conservative approach — any state except fully closed keeps the wakelock held. This is safer than only checking ESTABLISHED, since TIME-WAIT and CLOSE-WAIT still mean resources are in use.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| WSL2 not installed or WSL feature disabled | Toggle defaults to `false`; if accidentally `true`, detect and log warning, skip WSL2 monitoring |
| `/proc/net/tcp` missing or permission denied | Detect on first read, log warning, skip WSL2 monitoring |
| WSL restart kills subprocess | Pipe EOF detected by poll + process.poll(), subprocess restarted automatically |
| Python subprocess stdout buffer fills up | Drain pipe fully each cycle with poll; if line is too long, discard and continue |
| WSL2 distro reset by user (files wiped) | Helper script is rewritten on each startup; no state to preserve |
| WSL2 distro changed by user | Use `wsl.exe` (default distro) — consistent with user's active distro |
