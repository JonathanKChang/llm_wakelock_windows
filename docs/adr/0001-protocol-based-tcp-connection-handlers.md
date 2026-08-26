# ADR-0001: Protocol-based TCP Connection Source Abstraction

**Date**: 2026-05-09
**Status**: accepted
**Deciders**: Engineering team

## Context

`llm_wakelock_windows.py` was a ~250-line monolithic script handling two TCP connection sources: Windows native via `iphlpapi.GetExtendedTcpTable` and WSL via `/proc/net/tcp` parsing. Both sources had separate parsing logic, duplicate `is_monitored_active` functions, and SSH tracking used PID-based keys that were meaningless for WSL (always 0). There was no clean way to distinguish WSL connections from native Windows ones, making the code hard to maintain, test, and extend.

## Decision

Extract `WindowsTcpHandler` and `WslTcpHandler` into separate classes sharing a `TcpConnectionSource` Protocol (`get_connections() -> list[dict]`). Replace PID-based connection tracking with an `is_wsl: bool` flag on each connection dict. Merge duplicate functions into shared implementations. Keep wakelock logic (`acquire`/`release`) at module level.

## Alternatives Considered

### Alternative 1: Abstract Base Class (ABC)
- **Pros**: Enforces interface through inheritance; IDE tooling support for abstract method enforcement
- **Cons**: Requires explicit subclassing; heavier than needed for read-only interfaces
- **Why not**: Python's `typing.Protocol` provides structural subtyping without inheritance — lighter, easier to mock in tests, and idiomatic for this use case

### Alternative 2: Separate Connection Lists per Source
- **Pros**: Explicit separation; no flag needed on dicts
- **Cons**: More complex filtering logic; main loop must maintain two parallel data structures
- **Why not**: A single `is_wsl` flag on each connection dict simplifies the main loop to a unified data structure

### Alternative 3: Keep PID in Connection Dicts
- **Pros**: Preserves existing schema; no migration needed
- **Cons**: WSL always reports PID 0 (useless); SSH tracking with PID breaks on reconnects
- **Why not**: PID is never used for decision-making — ports + remote_addr are sufficient session discriminators

## Consequences

### Positive
- Clean separation of Windows and WSL concerns; each handler owns its data source
- Shared interface enables easy addition of new TCP sources (e.g., WSL2 Docker, hyper-v)
- Structural subtyping makes mocking in tests straightforward

### Negative
- Migration breaks `_make_conn` test helpers — must update all existing test code
- Slightly more code surface area with class boilerplate

### Risks
- [Risk] Removing PID from connection dicts requires updating all test helpers → [Mitigation] Update test_wakelock.py in the same change
