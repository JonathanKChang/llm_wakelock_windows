## Requirements

### Requirement: SubprocessDrain uses single-sentinel synchronous drain with queue.get(timeout)

The `SubprocessDrain` class SHALL use the existing sentinel marker (emitted at the end of each subprocess loop iteration, plus one upfront before the loop) to delimit drain windows. A background daemon thread reads stdout into a `queue.Queue`. The `drain()` method SHALL use `queue.get(timeout=remaining)` to block-wait for new lines (no busy-polling), accumulate all available lines, scan for the last two occurrences of the sentinel, return lines between them, and put back lines after the last sentinel for the next call. If fewer than two sentinels appear within `drain_timeout`, `drain()` SHALL NOT raise an exception; instead it SHALL return cached output (if any) or `[]`, increment a failure counter, and trigger subprocess auto-restart when the `max_consecutive_failures` threshold is reached.

#### Scenario: Normal drain returns lines between last two sentinel occurrences
- **WHEN** the subprocess has emitted `<sentinel>`, followed by output lines, followed by `<sentinel>` again, and `drain()` is called
- **THEN** `drain()` returns only the output lines between the last two sentinel occurrences (excluding the sentinels themselves)

#### Scenario: Empty output between two sentinels returns empty list
- **WHEN** the subprocess emits `<sentinel>` immediately followed by `<sentinel>` again (no output lines)
- **THEN** `drain()` returns an empty list `[]`

#### Scenario: Fewer than two sentinels returns cached output instead of raising exception
- **WHEN** `drain()` is called and fewer than two sentinel occurrences appear in the queue
- **THEN** `drain()` returns the cached output from the last successful drain, or `[]` if no prior output exists

#### Scenario: Sentinel failure threshold triggers subprocess auto-restart
- **WHEN** `drain()` fails to find a sentinel pair for `max_consecutive_failures` iterations (default: 10)
- **THEN** `drain()` restarts the subprocess (terminates old process, spawns new one) and resets the failure counter on successful restart

#### Scenario: Restart cooldown prevents rapid retry attempts
- **WHEN** `drain()` triggers a subprocess restart and the next call occurs before `wsl_recovery_interval` (default: 60s) has elapsed
- **THEN** `drain()` returns cached output without attempting another restart

#### Scenario: Multiple cycles — drain returns lines between last two sentinel occurrences
- **WHEN** the subprocess has produced multiple cycles (multiple sentinel occurrences) in the queue
- **THEN** `drain()` returns exactly the lines between the last two sentinel occurrences

#### Scenario: Partial output from previous cycle is retained in queue
- **WHEN** a previous `drain()` call found one sentinel with partial output after it (no second sentinel), and `drain()` is called again when the second sentinel has arrived
- **THEN** `drain()` returns the partial output from the previous call plus any new output up to the second sentinel

#### Scenario: drain() uses queue.get(timeout=remaining) with no busy-polling
- **WHEN** `drain()` needs to wait for more data from stdout
- **THEN** it uses `queue.get(timeout=remaining)` where `remaining` is the time left in `drain_timeout`, blocking until data arrives or timeout expires

#### Scenario: Process death detected via poll() triggers restart
- **WHEN** the subprocess process has terminated (detected via `poll() != None`) while drain is waiting for sentinels
- **THEN** `drain()` logs a warning with timestamp and owner name, attempts restart when cooldown permits, and returns cached or empty output

#### Scenario: Successful restart resets consecutive failure counter
- **WHEN** `drain()` has been restarting due to failures and the new subprocess begins producing output
- **THEN** the next successful sentinel pair found resets `_consecutive_failures` to 0

### Requirement: Configurable drain wait time via multiplier

The `SubprocessDrain` class SHALL accept a `drain_wait_multiplier` configuration parameter (default: 1.0). The drain timeout SHALL be computed as `polling_interval × drain_wait_multiplier`.

#### Scenario: Default multiplier gives one polling interval wait
- **WHEN** `drain_wait_multiplier` is not specified in config
- **THEN** the drain timeout equals the polling interval

#### Scenario: Custom multiplier scales wait time proportionally
- **WHEN** `drain_wait_multiplier` is set to 2.0 and polling interval is 5.0
- **THEN** the drain timeout is 10.0 seconds

#### Scenario: Multiplier of 0 disables drain
- **WHEN** `drain_wait_multiplier` is set to 0
- **THEN** the drain timeout is 0 and `drain()` returns immediately with `[]` if no data is available

### Requirement: TcpConnectionMonitor uses remaining-time sleep

The `TcpConnectionMonitor.run()` method SHALL measure elapsed time before and after `get_all_connections()`, then sleep only the remaining time until the next polling interval.

#### Scenario: Sleep remaining time after fast connection check
- **WHEN** `get_all_connections()` completes in 0.5 seconds with a 5.0 second polling interval
- **THEN** `TcpConnectionMonitor` sleeps for approximately 4.5 seconds

#### Scenario: Sleep zero when connection check exceeds interval
- **WHEN** `get_all_connections()` takes longer than the polling interval
- **THEN** `TcpConnectionMonitor` sleeps for 0 seconds (no sleep) and immediately starts the next iteration
