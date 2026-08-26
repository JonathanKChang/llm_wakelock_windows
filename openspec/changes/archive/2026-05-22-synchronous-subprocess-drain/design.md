## Context

`SubprocessDrain` runs a persistent subprocess in a loop, reading stdout into a thread-safe queue. The current `drain()` method is asynchronous — it grabs whatever is in the queue at call time, finds the last sentinel, and returns lines after it. This is fragile: stale lines from earlier polls mix with new output, and the caller has no guarantee of atomic reads per polling cycle.

The subprocess command is wrapped in a shell loop with sentinel at end of each iteration plus one upfront:
```
echo <sentinel>
while true; do <command> || break; echo <sentinel>; sleep <interval>; done
```

## Goals / Non-Goals

**Goals:**
- Deterministic, atomic reads: `drain()` returns exactly the output of one polling cycle
- No stale data: each `drain()` call is self-contained between sentinel markers
- Configurable wait time via multiplier on polling interval
- Tighter main-loop timing: `TcpConnectionMonitor` sleeps only remaining time

**Non-Goals:**
- Adding new protocols or external dependencies
- Handling partial/corrupted sentinel output (line-buffered reading ensures complete lines)

## Decisions

### 1. Single sentinel, two occurrences per cycle, emitted at end of iteration

**Decision:** Use the existing sentinel marker, emitted at the end of each loop iteration (after the command, before sleep) plus one upfront before the loop. `drain()` scans for the last two occurrences to delimit a drain window.

The subprocess loop:
```
echo <sentinel>
while true; do <command> || break; echo <sentinel>; sleep <interval>; done
```

`drain()` accumulates lines from the queue, scans for the last two sentinel occurrences, returns lines between them, and puts back lines after the last sentinel for the next call.

**Rationale:** Emitting the sentinel at the end of the iteration means when `drain()` finds two sentinels, the command output between them is already complete — no wasted wait for the next sentinel. Upfront sentinel ensures the first drain() call can find a complete cycle. One sentinel is simpler than two distinct markers.

### 2. Keep background thread + queue, use queue.get(timeout) for drain polling

**Decision:** Keep the background daemon thread that reads stdout into the existing `queue.Queue`. `drain()` uses `queue.get(timeout=remaining)` to block-wait for new lines (no busy-polling, no magic sleep numbers). Total wait capped at `drain_timeout`.

Implementation:
```python
def drain(self):
    start = time.time()
    all_lines = []
    while time.time() - start < self._drain_timeout:
        remaining = self._drain_timeout - (time.time() - start)
        if remaining <= 0:
            break
        try:
            line = self._queue.get(timeout=remaining)
            all_lines.append(line)
            # Drain all available lines immediately
            while not self._queue.empty():
                try:
                    all_lines.append(self._queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            break  # overall timeout reached
        
        idx = self._find_last_sentinel_pair(all_lines)
        if idx is not None:
            result = all_lines[idx[0]+1:idx[1]]
            leftover = all_lines[idx[1]+1:]
            for line in leftover:
                self._queue.put(line)
            return result
    
    for line in all_lines:
        self._queue.put(line)
    raise SentinelNotFound()
```

**Rationale:** `queue.get(timeout=remaining)` blocks until either data arrives or the overall timeout expires — no busy-waiting, no magic sleep numbers. After each line, we drain all available lines at once. Stale lines (before first sentinel of last pair) are discarded; lines after the last sentinel are put back for the next call.

### 3. Wait time = polling_interval × drain_wait_multiplier

**Decision:** Default wait time equals one full polling interval (multiplier = 1.0).

```python
self._drain_timeout = self._polling_interval * config.get("drain_wait_multiplier", 1.0)
```

**Rationale:** The subprocess loop sleeps `polling_interval` between iterations. A drain timeout of exactly one interval means `drain()` will wait for at most one full cycle. If the subprocess is slow (command takes longer than the interval), the multiplier can be increased (e.g., 2.0). Defaulting to 1.0 keeps the system responsive while giving the subprocess one full cycle to produce output.

### 4. TcpConnectionMonitor sleeps remaining time

**Decision:** Start a timer before `get_all_connections()`, then sleep `max(0, polling_interval - elapsed)`.

```python
start = time.time()
all_conns = self.get_all_connections()
elapsed = time.time() - start
remaining = self._config["polling_interval"] - elapsed
if remaining > 0:
    time.sleep(remaining)
```

**Rationale:** This ensures the loop period is at most `polling_interval` (not `polling_interval + command_execution_time`), keeping the monitoring frequency tight and predictable.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Subprocess hangs and never emits second sentinel | Overall timeout in `drain()` raises `SentinelNotFound`; caller handles by marking handler as stopped |
| Queue grows unbounded if subprocess produces data faster than drain() is called | Bounded by pipe buffer (~64KB); subprocess blocks on write when pipe is full. drain() discards processed lines. |
| `drain_wait_multiplier` too low causes missed output | Default 1.0 covers one full cycle; document that slow commands need higher values |
| queue.get(timeout) blocks the main thread during drain | Intentional — drain() is a synchronous call; caller expects to wait |
