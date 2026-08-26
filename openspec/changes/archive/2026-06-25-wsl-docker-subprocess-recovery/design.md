## Context

The monitoring tool uses `SubprocessDrain` to run persistent subprocesses (WSL `/proc/net/tcp`, Docker container TCP, docker ps discovery) and drain their stdout into a queue. When WSL or Docker shuts down (laptop sleep, `wsl --shutdown`, Docker Desktop restart), the subprocess dies and the current code permanently stops monitoring — requiring a manual script restart to recover.

`SentinelNotFound` was previously used as a signal from `SubprocessDrain` to handlers that recovery was needed, but this coupled the two layers and made handlers responsible for lifecycle management.

## Goals / Non-Goals

**Goals:**
- SubprocessDrain owns its own lifecycle: detects death, restarts automatically with cooldown
- Handlers are simplified — no recovery logic, just `drain()` and parse
- SubprocessDrain logs contextual messages (timestamp + owner name) for death/recovery events
- Remove `SentinelNotFound` exception as part of the refactor
- Single config parameter `wsl_recovery_interval` controls both restart cooldown and Docker discovery cadence

**Non-Goals:**
- Exponential backoff on restarts (fixed interval is sufficient; WSL recovers quickly)
- Handler-level recovery attributes or timer logic
- Changes to WindowsTcpHandler (uses iphlpapi, no subprocess — not affected)
- Changes to the wakelock main loop logic

## Decisions

### SubprocessDrain owns restart (not handlers)

**Decision**: `SubprocessDrain.drain()` detects process death via `_process.poll()`, increments a failure counter, and calls `self.restart()` internally when thresholds are met. Handlers simply call `drain()` and get back data or empty list.

**Why**: Eliminates the handler-level recovery pattern (timer, attributes, helper function) that required ~60 lines of code across two handlers. One source of truth for lifecycle.

```
Old: handler checks _stopped → check timer → construct new drain → start
New: handler calls drain() → drain detects death → restart internally
```

### Owner string injected into SubprocessDrain

**Decision**: `SubprocessDrain.__init__(command, ..., owner="subprocess")` accepts an owner string. Logging uses this for contextual messages: `"WSL /proc/net/tcp"`, `"Docker container abc123"`, etc.

**Why**: SubprocessDrain knows when to log but not what the message should say. The handler passes the owner at construction, keeping SubprocessDrain self-contained for logging.

**Alternative**: Callback function (`on_restart=callable`). Rejected — callback is more complex than a simple string, and every handler would need a lambda.

### Separate events: process death vs sentinel misses

**Decision**: Sentinel misses increment `_consecutive_failures` (for restart threshold). Process death is detected separately via `poll() != None` and triggers immediate warning logging + restart check. They share the same counter for the restart threshold but are distinct events.

**Why**: Sentinel misses can happen during load without actual failure. Logging every miss would be noisy. Process death is a significant event worth notifying about once, then retrying silently.

### Config: `wsl_recovery_interval` replaces `wsl_docker_discovery_interval`

**Decision**: Single parameter (default 60s) used by SubprocessDrain for restart cooldown AND by WslDockerManager for discovery cadence.

**Why**: Both intervals are semantically "how often to check/try WSL/Docker things." One config avoids drift between them. The name `wsl_recovery_interval` reflects the broader use beyond just Docker discovery.

**Alternative**: Two separate params. Rejected — adds config complexity for two values that should logically be the same (recovery and discovery cadence).

### SentinelNotFound exception removed

**Decision**: Remove entirely. No replacement signal needed since SubprocessDrain handles everything internally.

**Why**: The exception was only used as a coupling mechanism between drain and handler layers. With drain owning restart, the exception has no consumer.

## Risks / Trade-offs

[Risk] SubprocessDrain restarting too aggressively during WSL flapping (rapid on/off cycles)
→ Mitigation: `wsl_recovery_interval` cooldown (60s default) limits restart attempts. Process death detection triggers warning logging so user sees what's happening.

[Risk] drain() being called during stop()/cleanup() race condition
→ Mitigation: `stop()` sets `_stopped = True`. After cleanup, the handler won't call `drain()` anymore. The 0.5s sleep in `restart()` between stop and start lets the old thread exit cleanly.

[Risk] Config rename breaking existing user configs
→ Mitigation: `wsl_recovery_interval` is a new key in DEFAULTS. Old config using `wsl_docker_discovery_interval` will still have it in their config.toml but won't be read. Since the default is the same (60s), behavior for existing users is unchanged.

[Risk] Consecutive failures counter counting both sentinel misses and process deaths
→ Mitigation: Both events indicate the subprocess isn't producing useful data. The threshold of 10 combined with 60s cooldown means worst case: ~50 seconds of sentinel misses + restart attempt. User sees death warning immediately.

## Migration Plan

No migration needed. The change is backward compatible for existing users:
- Existing config files using `wsl_docker_discovery_interval` retain that key but it's no longer read; the default 60s applies instead (same value).
- No API changes visible to external code — SubprocessDrain is internal.
- Running instances continue monitoring until next restart; new instances get auto-recovery from startup.
