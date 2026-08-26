## Why

`SubprocessDrain` currently uses an async queue-based model where `drain()` reads from a background thread's queue. This makes the API brittle — `drain()` can return stale data from previous calls, and the caller has no way to know if the subprocess produced output between polls. Making it synchronous with explicit sentinel markers gives callers deterministic, atomic reads of each polling cycle's output.

## What Changes

- Replace the current fragile drain with a deterministic sentinel-delimited drain: `drain()` uses `queue.get(timeout=remaining)` to block-wait for new lines (no busy-polling, no magic sleep numbers), accumulates all available lines, scans for the last two sentinel occurrences, returns lines between them, and puts back lines after the last sentinel for the next call; raises `SentinelNotFound` if fewer than two markers appear within the configured wait time. Sentinel is emitted at the end of each loop iteration (after the command) plus one upfront before the loop.
- Add a `drain_wait_multiplier` config parameter (default: 1.0) that multiplies the polling interval to determine how long `drain()` waits for both sentinels.
- Update `TcpConnectionMonitor.run()` to sleep only the remaining time until the next polling interval (instead of sleeping the full interval), using a timer started before the drain call.

## Capabilities

### New Capabilities
- `synchronous-drain`: Deterministic, sentinel-delimited subprocess output reading with configurable wait time

### Modified Capabilities
- None — behavior change is internal to `SubprocessDrain` and `TcpConnectionMonitor`; external API contracts (connection discovery, wakelock management) remain unchanged.

## Impact

- **Code**: `tcp_handlers.py` — `SubprocessDrain` class (full rewrite), `TcpConnectionMonitor.run()` (sleep timing adjustment)
- **Config**: New optional `drain_wait_multiplier` key in `config.toml`
- **No breaking external API**: `drain()` still returns `list[str]`; callers still catch `SentinelNotFound`
