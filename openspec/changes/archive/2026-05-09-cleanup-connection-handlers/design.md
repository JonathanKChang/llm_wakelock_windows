## Context

`llm_wakelock_windows.py` is a ~250-line monolithic script that handles two TCP connection sources:
- **Windows native**: via `iphlpapi.GetExtendedTcpTable` with PID info
- **WSL**: via `/proc/net/tcp` parsed from a persistent subprocess

Both sources have separate parsing, separate `is_monitored_active` functions, and SSH tracking uses PID-based keys that are meaningless for WSL (always 0). The main loop duplicates connection collection and filtering logic for both sources.

## Goals / Non-Goals

**Goals:**
- Extract `WindowsTcpHandler` and `WslTcpHandler` into separate classes
- Define a `TcpConnectionSource` Protocol so both share `get_connections() -> list[dict]`
- Merge `is_monitored_active` and `is_wsl_monitored_active` into one function
- Merge `is_ssh_active` to work with both sources using a connection flag instead of PID
- Remove PID from connection dicts (not used for anything meaningful)
- Add `is_wsl: bool` to each connection dict to distinguish origins
- Keep the main loop, wakelock logic, and config loading at module level

**Non-Goals:**
- No new features or port-monitoring behavior changes
- No changes to `config.toml` schema
- No extraction of wakelock (`acquire`/`release`) into a class
- No changes to test_wakelock.py test cases (only helpers updated)

## Decisions

### 1. Use `typing.Protocol` instead of ABC
**Decision**: Use `Protocol` for the shared interface.
**Rationale**: Python's structural subtyping (protocols) is lighter than ABCs — no inheritance required, easier to test with mocks, and idiomatic for read-only interfaces like `get_connections()`.

### 2. Add `is_wsl: bool` field to connection dicts instead of separate lists
**Decision**: Each connection dict gets an `is_wsl` boolean field.
**Rationale**: A single flag is simpler than maintaining separate connection lists. The main loop can filter or label connections per-source using this field. It also lets `is_ssh_active` build keys without PIDs.

### 3. Remove PID from connection dicts entirely
**Decision**: Drop `pid` from both Windows and WSL connection dicts.
**Rationale**: PID is never used for decision-making — `is_monitored_active` and `is_ssh_active` only check ports. SSH tracking currently uses PID in the key, but the key should be `(local_port, remote_port, remote_addr)` which is sufficient to distinguish sessions. WSL always has PID 0 anyway.

### 4. SSH key changes from `(pid, local_port, remote_port, remote_addr)` to `(local_port, remote_port, remote_addr)`
**Decision**: Remove PID from SSH tracking keys.
**Rationale**: Same session can reconnect with a different PID (normal SSH behavior). Using ports + remote_addr is the correct discriminator. The reconnect detection (stale key pruning) already handles this.

### 5. WSL subprocess management stays inside `WslTcpHandler`
**Decision**: All WSL subprocess lifecycle (deploy, start, drain, restart) lives inside the class.
**Rationale**: Keeps WSL concerns fully encapsulated. The class owns its subprocess state.

## Risks / Trade-offs

[Risk] Removing PID breaks test_wakelock.py `_make_conn` helper and blob parsing tests.
→ [Mitigation] Update test helpers to not include `pid` field; blob parser in test_wakelock.py can drop it or keep it as optional for backward compatibility.

[Risk] WSL subprocess management is complex (thread, queue, restart logic).
→ [Mitigation] Move all existing WSL subprocess functions into `WslTcpHandler.__init__` and `get_connections`; no logic is removed, only moved.

[Risk] Main loop needs to know which source a connection came from for logging.
→ [Mitigation] `is_wsl` flag on each connection dict allows the main loop to label log lines as `[win]` or `[wsl]`.

## Migration Plan

1. Create `openspec/changes/cleanup-connection-handlers/` with proposal, design, specs, tasks
2. Implement: extract classes, protocol, merge functions, update main loop
3. Update test helpers to match new dict schema
4. Run tests to verify no behavior change
5. Archive change

## Open Questions

None — all decisions derived from user requirements and code analysis.
