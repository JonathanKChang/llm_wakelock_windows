# LLM Wakelock for Windows / Generic Port Activity Wakelock

A generic Windows tool that keeps your system awake while TCP connections on configured ports are active.

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

The `SSH_MIN_DURATION` threshold (default: 30 seconds) prevents short-lived SSH connections from triggering the wakelock. For example, a `git fetch` over SSH typically completes in a few seconds — you don't want that to keep your machine awake. Only sustained SSH sessions will trigger the lock.

## Configuration

Edit the port lists and thresholds near the top of `llm_wakelock_windows.py`:

```python
# Ports monitored with instant detection (any established connection counts)
# Defaults: 8080 (llama.cpp server), 11434 (Ollama)
LOCAL_MONITORED_PORTS = [8080, 11434]
REMOTE_MONITORED_PORTS = [8080, 11434]

# SSH connections require this minimum duration before counting as active
LOCAL_SSH_PORTS = [22]
REMOTE_SSH_PORTS = [22]
SSH_MIN_DURATION = 30.0   # seconds — prevents short scripts (git fetch, etc.) from triggering
POLLING_INTERVAL = 5.0    # seconds
```

### Adding a new service

Just add its port to `LOCAL_MONITORED_PORTS` or `REMOTE_MONITORED_PORTS`:

```python
LOCAL_MONITORED_PORTS = [8080, 11434, 5432]  # llama.cpp + Ollama + local PostgreSQL
```

## Files

| File | Purpose |
|---|---|
| `llm_wakelock_windows.py` | Main daemon — run on Windows |
| `dump_iphlpapi.py` | Utility: dumps raw TCP table to a binary file for analysis |
| `test_wakelock.py` | Tests: parses a TCP table blob (skips if missing) + SSH tracking logic (pure Python, runs on any platform) |

## Requirements

- Windows (uses `iphlpapi.GetExtendedTcpTable` and `kernel32.SetThreadExecutionState`)
- Python 3.12+

## Running

```bash
python llm_wakelock_windows.py
```

The script runs indefinitely. It prints the current time and relevant connection details whenever a wakelock is acquired.
