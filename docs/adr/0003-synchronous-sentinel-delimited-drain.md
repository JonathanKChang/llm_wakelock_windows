# ADR-0003: Synchronous Sentinel-Delimited Subprocess Drain

**Date**: 2026-05-22
**Status**: accepted
**Deciders**: Engineering team

## Context

`SubprocessDrain` used an async queue-based model where `drain()` read from a background thread's queue. This was fragile — `drain()` could return stale data from previous calls, and the caller had no guarantee of atomic reads per polling cycle. Lines from earlier polls mixed with new output, making it impossible to know exactly what data each drain call received.

## Decision

Replace the async drain with a deterministic sentinel-delimited model: `drain()` uses `queue.get(timeout=remaining)` to block-wait for new lines, accumulates all available lines, scans for the last two sentinel occurrences, returns lines between them, and puts back lines after the last sentinel for the next call. Raises `SentinelNotFound` if fewer than two sentinels appear within the configured wait time (`polling_interval × drain_wait_multiplier`). The subprocess emits one sentinel upfront (before the loop) and one at the end of each iteration. `TcpConnectionMonitor` sleeps only remaining time until next interval.

## Alternatives Considered

### Alternative 1: Per-cycle Sentinel Count Check
- **Pros**: Explicit counting; no need to scan for pairs
- **Cons**: Requires tracking state across drain calls; more complex queue management
- **Why not**: Scanning for the last two sentinels in accumulated lines is simpler — `drain()` is fully self-contained

### Alternative 2: Two Different Sentinel Markers
- **Pros**: Could distinguish "upfront" from "end-of-cycle" markers
- **Cons**: More complex subprocess script; no meaningful semantic difference between markers
- **Why not**: One sentinel is sufficient and simpler. The pair semantics (last two) define the drain window.

### Alternative 3: Always Drain All Available Lines, No Timeout
- **Pros**: Never raises `SentinelNotFound`; always returns data
- **Cons**: Could block indefinitely if subprocess hangs; no backpressure signal to caller
- **Why not**: The timeout + `SentinelNotFound` exception gives the caller a clear failure signal to trigger handler recovery

## Consequences

### Positive
- Deterministic, atomic reads — each `drain()` returns exactly one polling cycle's output
- No stale data from previous calls; lines after the last sentinel are preserved for the next drain
- Configurable wait time via `drain_wait_multiplier` handles slow commands gracefully

### Negative
- `drain()` now blocks the calling thread during wait — callers must expect synchronous behavior
- More complex drain loop with sentinel pair scanning logic

### Risks
- [Risk] Subprocess hangs and never emits second sentinel → [Mitigation] Overall timeout in `drain()` raises `SentinelNotFound`; caller handles recovery
- [Risk] Queue grows unbounded if subprocess produces data faster than drain calls → [Mitigation] Pipe buffer (~64KB) naturally bounds this; subprocess blocks on write
