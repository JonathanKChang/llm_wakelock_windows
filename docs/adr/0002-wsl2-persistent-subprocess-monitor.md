# ADR-0002: WSL2 Persistent Subprocess for /proc/net/tcp Monitoring

**Date**: 2026-05-09
**Status**: accepted
**Deciders**: Engineering team

## Context

The monitor only queried Windows TCP connections via `iphlpapi.GetExtendedTcpTable`, but WSL2 runs in a separate virtualized network stack. Connections to WSL2-hosted services (e.g., an LLM server running inside WSL2) were invisible to Windows APIs, so the wakelock was never acquired even when active work was happening inside WSL2. WSL2 exposes TCP state via `/proc/net/tcp` — a kernel file with hex-encoded fields that Python can parse.

## Decision

When `enable_wsl2_monitoring` config toggle is `true`, maintain a persistent subprocess (`wsl.exe -e bash ~/bin/wsl2_tcp_monitor.sh`) that runs `cat /proc/net/tcp` continuously. Python drains lines from the subprocess stdout pipe on each poll cycle, parses hex-encoded fields into connection dicts, and integrates them into the same monitoring loop as Windows connections using shared port/SSH config. Detect subprocess death via `select.poll()` + `process.poll()` and auto-restart.

## Alternatives Considered

### Alternative 1: Per-poll `wsl.exe` Spawn
- **Pros**: Simpler — no persistent process to manage
- **Cons**: Adds ~50-100ms overhead per 5s poll cycle; WSL startup latency is significant
- **Why not**: Persistent subprocess gives near-real-time detection with essentially zero overhead

### Alternative 2: Separate WSL2 Port List in Config
- **Pros**: Independent control over which ports to monitor on each platform
- **Cons**: Config duplication; same ports should apply regardless of platform
- **Why not**: The user wants the same ports monitored whether the service runs on Windows or WSL2. A single toggle keeps config simple.

### Alternative 3: Parse State Codes in Bash
- **Pros**: Python receives already-filtered connections (ESTABLISHED only)
- **Cons**: Fragile bash parsing logic; hard to test
- **Why not**: `cat /proc/net/tcp` is minimal and never fails. All parsing stays in Python where it's testable and maintainable.

### Alternative 4: Only Check ESTABLISHED State (0x01)
- **Pros**: More precise — only counts active connections
- **Cons**: Misses TIME-WAIT, CLOSE-WAIT states that still hold resources
- **Why not**: Conservative approach — any state except `01` (CLOSE) keeps the wakelock held. This is safer since resource-releated states still justify holding the lock.

## Consequences

### Positive
- WSL2 connections are now fully visible to the monitoring system
- Persistent subprocess gives minimal overhead and near-real-time detection
- Single config toggle — no duplicate port lists or complexity

### Negative
- Adds subprocess lifecycle management (startup, restart on death)
- Bash helper script deployed to WSL2 must survive distro resets (rewritten each startup)

### Risks
- [Risk] WSL2 not installed or feature disabled → [Mitigation] Toggle defaults to `false`; detect and warn if accidentally enabled
- [Risk] `/proc/net/tcp` missing or permission denied → [Mitigation] Detect on first read, log warning, skip WSL2 monitoring
- [Risk] WSL restart kills subprocess → [Mitigation] Pipe EOF detected by `poll()` + `process.poll()`, subprocess restarted automatically
