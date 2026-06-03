# LLM Wakelock for Windows
_A Generic Container-Aware Port Activity Monitor_

A tool that keeps your system awake while TCP connections on configured ports are active in any container.

## Description

### Why This Exists

I can't be the only one who's repurposed a gaming Windows PC to serve LLMs, since they sit idle when not gaming. But when you're not in a late night vibe-coding session, you might want to save some energy. Manually waking and sleeping machines is a chore, while on the other end Windows / WSL doesn't grab a wakelock for active incoming ssh connections or even high computation LLM inference. Designed in a way that is easy to extend to Linux and MacOS either way.

Note that Windows, WSL, and docker network stacks are all isolated at the kernel level, regardless of whther the interfaces and IPs are isolated. Additionally, calls from windows to WSL are surprisingly unreliable especially under any load, so this tool creates persistent lightweight helper processes that are resilient in order to monitor WSL and Docker container lifecycles.

### What It Does

- Polls the system's TCP connection table at a configured interval.
- When an established connection is detected on any **monitored port**, it acquires a Windows wakelock (preventing sleep/hibernate).
- When all monitored connections drop, it releases the wakelock.

### Energy Efficiency

This tool works best on machines where you want to **optimize power usage** — letting the system sleep when idle, but keeping it awake when you need it. These common activities do not grab a wakelock and will allow Windows to sleep, whether running natively in Windows, in WSL, or in a Docker:
- **Long agentic sessions** — local inference servers (llama.cpp, Ollama, etc.), despite using significant CPU/GPU 
- **SSH sessions** — Active SSH sessions, incoming or outgoing.

### A typical setup:

- Windows Gaming PC
   - Serves LLM Inference
   - Hosts WSL running an agentic harness in a tmux
   - Automatically set to sleep when idle
- Remote access - **Laptop or Phone**
   - Manually or automatically send a **Wake-on-LAN magic packet** to the PC if needed
   - Check in on the agents via SSH over tailscale

## Prerequisites

- Windows (uses `iphlpapi.GetExtendedTcpTable` and `kernel32.SetThreadExecutionState`)
- Python 3.12+

## Installation

Copy the following files to any directory:

- `llm_wakelock_windows.py` — Main daemon script
- `tcp_handlers.py` — TCP connection handlers
- `config.toml` — Configuration file (optional; copy and uncomment values to override defaults)

No installation or dependencies are required.

## Running

```bash
python llm_wakelock_windows.py
```

The script runs indefinitely. It prints the current time and relevant connection details whenever a wakelock is acquired.

## Configuration

Edit `config.toml` in the script directory and uncomment the values you want to override. The file is optional — if it doesn't exist, all built-in defaults are used.

### Main Settings

| Setting | Default | Description |
|---|---|---|
| `local_monitored_ports` | `[8080, 11434]` | Local ports for instant wakelock |
| `remote_monitored_ports` | `[8080, 11434]` | Remote ports for instant wakelock |
| `local_ssh_ports` | `[]` | Local SSH ports |
| `remote_ssh_ports` | `[]` | Remote SSH ports |
| `ssh_min_duration` | `30.0` | Min SSH session duration (seconds) |
| `polling_interval` | `5.0` | Polling interval (seconds) |
| `grace_period_minutes` | `5.0` | How long to extend wakelock after last active connection |
| `wsl_monitoring` | `false` | Monitor WSL2 TCP connections |
| `wsl_docker_monitoring_max` | `0` | Max Docker containers to monitor (0 = disabled) |

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

#### SSH minimum duration?

The `ssh_min_duration` threshold prevents short-lived SSH connections from triggering the wakelock. For example, a `git fetch` over SSH typically completes in a few seconds — you don't want that to keep your machine awake. Only sustained SSH sessions will trigger the lock.

### Per-source port / SSH overrides

By default, all connection sources (Windows, WSL, Docker) share the same global port and SSH settings. To apply different ports to specific sources, use TOML sections:

```toml
# Global defaults — applied to all sources unless overridden
local_monitored_ports = [8080]
remote_monitored_ports = [11434]

[wsl]
local_monitored_ports = [5555]   # WSL only monitors local port 5555, still monitors remote port 11434

[wsl_docker]
remote_monitored_ports = [443]  # Docker containers only monitor remote port 443, still monitors remote port 11434
```

Each section supports: `local_monitored_ports`, `remote_monitored_ports`, `local_ssh_ports`, `remote_ssh_ports`. Omitted fields fall back to the global defaults.

Available sections: `[windows]`, `[wsl]`, `[wsl_docker]`.

### Docker container monitoring

Monitor Docker containers running inside WSL by setting `wsl_docker_monitoring_max` to a positive number. The tool auto-discovers running containers via `docker ps` at startup and spawns a persistent subprocess per container to read `/proc/net/tcp`.

```toml
wsl_docker_monitoring_max = 5  # monitor up to 5 containers
```

> **Note:** Container discovery runs every `wsl_recovery_interval` seconds. New containers are picked up automatically.

> **Note:** Connections are labeled `[docker:<container_id>]` in the output.

## Tests

See [tests/README.md](tests/README.md) for full test documentation.
