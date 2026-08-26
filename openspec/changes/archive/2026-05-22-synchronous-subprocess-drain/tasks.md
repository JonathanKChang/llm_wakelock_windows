## 1. SubprocessDrain — new synchronous implementation

- [ ] 1.1 Keep background read thread and queue.Queue; keep existing _queue attribute
- [ ] 1.2 Update _full_command: emit sentinel upfront before loop, then emit at end of each iteration (after command, before sleep) — ensures drain() never waits unnecessarily for next sentinel
- [ ] 1.3 Implement _find_last_sentinel_pair(lines) helper that scans a list of lines for the last two sentinel occurrences and returns (first_idx, second_idx) or None
- [ ] 1.4 Implement drain() method: use queue.get(timeout=remaining) to block-wait for new lines, accumulate all available lines, call _find_last_sentinel_pair; if found return lines between them and put back lines after last sentinel; if not found, loop until drain_timeout then raise SentinelNotFound
- [ ] 1.5 Add drain_wait_multiplier config parameter and compute drain timeout as polling_interval × multiplier
- [ ] 1.6 No queue.Queue removal — keep existing thread-safe queue, only change drain() logic

## 2. TcpConnectionMonitor — remaining-time sleep

- [ ] 2.1 In TcpConnectionMonitor.run(), add elapsed-time tracking: start timer before get_all_connections()
- [ ] 2.2 Replace `time.sleep(self._config["polling_interval"])` with `time.sleep(max(0, polling_interval - elapsed))`

## 3. Config and defaults

- [ ] 3.1 Add `drain_wait_multiplier` to DEFAULTS dict in llm_wakelock_windows.py with default value 1.0
- [ ] 3.2 Pass polling_interval to SubprocessDrain so it can compute drain timeout from multiplier

## 4. Tests

- [ ] 4.1 Test: drain returns lines between last two sentinels (happy path)
- [ ] 4.2 Test: drain returns empty list when subprocess produces no output between sentinels
- [ ] 4.3 Test: drain raises SentinelNotFound when second sentinel never arrives within timeout
- [ ] 4.4 Test: drain returns only the last cycle's output when multiple cycles are buffered
- [ ] 4.5 Test: partial output after last sentinel is retained in queue for next drain() call
- [ ] 4.6 Test: drain_wait_multiplier scales the timeout correctly
- [ ] 4.7 Test: TcpConnectionMonitor sleeps remaining time (mock get_all_connections to return quickly)
