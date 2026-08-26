## Why

The main script `llm_wakelock_windows.py` is a ~250-line monolith mixing WSL subprocess management, Windows TCP table parsing, connection checking, and wakelock control. WSL and Windows connection handling are duplicated with separate `is_monitored_active` functions, SSH tracking uses Windows-specific PID keys that don't apply to WSL, and there's no clean way to distinguish WSL connections from native Windows ones. This makes the code hard to maintain, test, and extend.

## What Changes

- Extract `WindowsTcpHandler` class for Windows `iphlpapi.GetExtendedTcpTable` parsing
- Extract `WslTcpHandler` class for WSL `/proc/net/tcp` polling via subprocess
- Define a `TcpConnectionSource` Protocol so both handlers share the same interface (`get_connections() -> list[dict]`)
- Merge `is_monitored_active` and `is_wsl_monitored_active` into a single shared function
- Merge `is_ssh_active` to work with both handlers — use a connection-level `is_wsl: bool` flag instead of PID-based keys
- Remove PID from connection dicts entirely (not used for anything meaningful); add `is_wsl` boolean field to distinguish connection origins
- Consolidate the main loop to call handler methods through the shared interface
- Keep `acquire`/`release` wakelock logic as module-level functions (Windows-specific, no extraction needed)

## Capabilities

### New Capabilities
- `connection-handlers`: Protocol-based TCP connection source abstraction with `WindowsTcpHandler` and `WslTcpHandler` implementations sharing a unified interface

### Modified Capabilities
- *(none — behavior is unchanged, only implementation is restructured)*

## Impact

- **Files**: `llm_wakelock_windows.py` — major refactor into classes; `test_wakelock.py` — update test helpers to use new class interface
- **No breaking config changes**: same `config.toml` keys, same behavior
- **No new dependencies**: still pure Python + Windows APIs only
