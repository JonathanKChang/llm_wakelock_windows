# ADR-0004: SubprocessDrain Owns Its Lifecycle with Auto-Restart

**Date**: 2026-06-25
**Status**: accepted
**Deciders**: Engineering team

## Context

When WSL or Docker shuts down (laptop sleep, `wsl --shutdown`, Docker Desktop restart), all monitoring subprocesses died and handlers permanently stopped — requiring a manual script restart to recover. This caused silent loss of connection tracking for 5–30+ seconds. The previous design used `SentinelNotFound` exceptions as a signal from `SubprocessDrain` to handlers that recovery was needed, coupling the two layers and making handlers responsible for ~60 lines of lifecycle management code (timers, flags, helper functions).

## Decision

`SubprocessDrain` owns its own restart logic: detects process death via `process.poll()`, increments a consecutive failures counter, and restarts internally when thresholds are met. Handlers simply call `drain()` and get back data or an empty list — no recovery logic. Remove `SentinelNotFound` exception entirely since it was only a coupling mechanism. Inject an owner string into SubprocessDrain for contextual logging (e.g., "WSL /proc/net/tcp", "Docker container abc123"). Use a single config parameter `wsl_recovery_interval` for both restart cooldown and Docker discovery cadence.

## Alternatives Considered

### Alternative 1: Callback Function for Restart Events
- **Pros**: Flexible; handler could perform custom actions on restart
- **Cons**: More complex API; every handler would need a lambda
- **Why not**: A simple owner string is sufficient for logging. Extra flexibility adds complexity without clear benefit.

### Alternative 2: Two Separate Config Parameters (Recovery + Discovery)
- **Pros**: Independent tuning of each interval
- **Cons**: Adds config complexity for two values that should logically be the same
- **Why not**: Both intervals semantically mean "how often to check/try WSL/Docker things." One config avoids drift and keeps it simple.

### Alternative 3: Exponential Backoff on Restarts
- **Pros**: Gentler on rapidly flapping systems (e.g., frequent laptop sleep/wake cycles)
- **Cons**: Adds complexity; WSL recovers quickly enough that a fixed interval is sufficient
- **Why not**: Fixed 60s cooldown is adequate. Exponential backoff adds code and tuning parameters for marginal benefit.

## Consequences

### Positive
- Eliminates ~60 lines of handler-level recovery code across two handlers
- Single source of truth for subprocess lifecycle management
- Handlers are simplified to just `drain()` + parse — no timers, flags, or recovery helpers
- Contextual logging (owner string) makes debugging subprocess failures easier

### Negative
- SubprocessDrain now has internal state (failure counter, restart logic) that wasn't visible to callers before
- Config rename (`wsl_docker_discovery_interval` → `wsl_recovery_interval`) breaks existing configs silently (same default value, so behavior unchanged for users with defaults)
- `SentinelNotFound` removal changes failure semantics — subprocess failures are now handled transparently instead of surfacing as exceptions

### Risks
- [Risk] SubprocessDrain restarting too aggressively during WSL flapping → [Mitigation] 60s cooldown limits restart attempts; process death warnings logged so user sees what's happening
- [Risk] Config rename breaking existing user configs → [Mitigation] Default is the same value (60s), so behavior for existing users with defaults is unchanged
