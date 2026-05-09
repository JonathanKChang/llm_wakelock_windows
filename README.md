# LLM Wakelock for Windows / Generic Port Activity Wakelock

A generic Windows tool that keeps your system awake while TCP connections on configured ports are active.

## Versions

| Version | Description |
|---|---|
| **v1** | Windows-only TCP port monitoring |
| **v2** | Added basic WSL and WSL Docker monitoring (container discovery runs once at startup) |

## Why Windows?

I can't be the only one who's repurposed a gaming Windows PC to serve LLMs, since they sit idle when not gaming. But when you're not in a late night vibe-coding session, you might want to save some energy. Manually waking and sleeping machines is a chore, while on the other end Windows / WSL doesn't grab a wakelock for active incoming ssh connections or even high computation LLM inference.

## What it does

- Polls the system's TCP connection table at a configured interval.
- When an established connection is detected on any **monitored port**, it acquires a Windows wakelock (preventing sleep/hibernate).
- When all monitored connections drop, it releases the wakelock.

## Best suited for power-optimized machines

This tool works best on machines where you want to **optimize power usage** — letting the system sleep when idle, but keeping it awake when you need it. Hybrid sleep may need to be disabled.

A typical setup:

1. The machine sleeps when idle to save power.
2. You (or an automation script) send a **Wake-on-LAN magic packet** to wake it when you need to access it.
3. Once awake, this tool prevents the machine from sleeping **during** active work:
   - **Long LLM sessions** — local inference servers (llama.cpp, Ollama) don't grab a wakelock on their own, so the machine could sleep mid-generation. This tool watches the server ports and keeps the system awake.
   - **SSH sessions** — whether you're SSH'd into this machine or its WSL or SSH'd out to another one, this prevents sleep during active remote work.

### Why the SSH minimum duration?

The `ssh_min_duration` threshold (default: 30 seconds) prevents short-lived SSH connections from triggering the wakelock. For example, a `git fetch` over SSH typically completes in a few seconds — you don't want that to keep your machine awake. Only sustained SSH sessions will trigger the lock.

## Configuration

Copy `config.toml` from the script directory and uncomment the values you want to override. The file is optional — if it doesn't exist, all built-in defaults are used.

### Built-in defaults

| Setting | Default | Description |
|---|---|---|
| `local_monitored_ports` | `[8080, 11434]` | Local ports for instant wakelock |
| `remote_monitored_ports` | `[8080, 11434]` | Remote ports for instant wakelock |
| `local_ssh_ports` | `[]` | Local SSH ports |
| `remote_ssh_ports` | `[]` | Remote SSH ports |
| `ssh_min_duration` | `30.0` | Min SSH session duration (seconds) |
| `polling_interval` | `5.0` | Polling interval (seconds) |
| `wsl_monitoring` | `false` | Monitor WSL2 TCP connections |
| `wsl_docker_monitoring_max` | `0` | Max Docker containers to monitor (0 = disabled) |

Example `config.toml`:

```toml
# Uncomment and change to override defaults
# local_monitored_ports = [8080, 11434]
# ssh_min_duration = 30.0
```

### Adding a new service

Add its port to the monitored port lists:

```toml
local_monitored_ports = [8080, 11434, 5432]  # llama.cpp + Ollama + local PostgreSQL
```

### Adding SSH support

Enable wakelock for SSH sessions by uncommenting and setting the SSH port:

```toml
local_ssh_ports = [22]
remote_ssh_ports = [22]
```

> **Warning:** Before adding ports to `local_ssh_ports`, verify your incoming SSH TCP connection behavior. Many systems leave SSH sessions open indefinitely (depending on SSH and kernel TCP keepalive settings), which would prevent your machine from ever sleeping.

### Docker container monitoring

Monitor Docker containers running inside WSL by setting `wsl_docker_monitoring_max` to a positive number. The tool auto-discovers running containers via `docker ps` at startup and spawns a persistent subprocess per container to read `/proc/net/tcp`.

```toml
wsl_docker_monitoring_max = 5  # monitor up to 5 containers
```

> **Note:** Container discovery runs once at startup. New containers started after the daemon begins are **not** picked up — restart the daemon to pick up new containers.

> **Note:** Connections are labeled `[docker:<container_id>]` in the output.


## Files

| File | Purpose |
|---|---|
| `llm_wakelock_windows.py` | Main daemon — run on Windows |
| `dump_iphlpapi.py` | Utility: dumps raw TCP table to a binary file for analysis |
| `wsl_tcp_monitor.sh` | WSL helper: reads `/proc/net/tcp` |
| `tests/test_wakelock.py` | Tests: parses a TCP table blob (skips if missing) + SSH tracking logic (pure Python, runs on any platform) |

## Requirements

- Windows (uses `iphlpapi.GetExtendedTcpTable` and `kernel32.SetThreadExecutionState`)
- Python 3.12+

## Running

```bash
python llm_wakelock_windows.py
```

The script runs indefinitely. It prints the current time and relevant connection details whenever a wakelock is acquired.

## Testing

Run tests with pytest (install first if needed):

```bash
pip install pytest
python -m pytest tests/test_wakelock.py -v
```

Or run directly (tests that require Windows will be skipped):

```bash
python tests/test_wakelock.py
```
