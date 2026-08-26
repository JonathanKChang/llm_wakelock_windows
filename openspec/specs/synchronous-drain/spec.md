## Purpose

Provides reliable output collection from a long-running subprocess, delivering new lines since the last read, with automatic recovery on process failure and graceful degradation when output is unavailable.

## Requirements

### Requirement: Backpressure-driven output collection
The drain mechanism SHALL block-wait for new subprocess output (no busy-polling), returning all lines that have arrived since the last collection. If no output has arrived within the configured wait time, it returns cached output from the previous successful read or an empty list if no prior read succeeded.

#### Scenario: New output is returned
- **WHEN** the subprocess has produced output lines since the last drain call
- **THEN** the drain returns exactly those new lines

#### Scenario: No output between calls
- **WHEN** the subprocess produced no output between two drain calls
- **THEN** the drain returns an empty list

#### Scenario: Fewer than expected markers returns cached output
- **WHEN** the drain is called but the expected delimiters are incomplete
- **THEN** it returns the previously cached output instead of raising an error

### Requirement: Failure threshold triggers auto-restart
When the drain fails to collect a complete set of output for a configurable number of consecutive attempts, it SHALL restart the subprocess (terminate the old one and spawn a new one), resetting the failure counter on a successful restart.

#### Scenario: Accumulated failures trigger restart
- **WHEN** the drain misses output delimiters for N consecutive attempts (where N is the configured threshold)
- **THEN** the subprocess is automatically restarted

#### Scenario: Successful restart resets failure count
- **WHEN** a restarted subprocess begins producing valid output
- **THEN** the consecutive failure counter is reset to zero

### Requirement: Restart cooldown prevents rapid retries
After triggering a subprocess restart, the drain SHALL wait for a configured minimum interval before attempting another restart. During cooldown, it returns cached output without retrying.

#### Scenario: Cooldown blocks rapid restart attempts
- **WHEN** a restart has just occurred and another call happens before the cooldown expires
- **THEN** no new restart is attempted; cached output is returned

### Requirement: Process death detection
The drain SHALL detect when the subprocess has terminated (via process status check) while waiting for output, and handle it as a failure condition that may trigger restart.

#### Scenario: Terminated process triggers failure handling
- **WHEN** the subprocess has exited before expected output arrives
- **THEN** the drain treats it as a collection failure and handles it per the failure/cooldown rules

### Requirement: Configurable output wait time
The maximum time the drain waits for new output SHALL be configurable. By default, the wait equals the polling interval. A multiplier parameter scales this proportionally; a value of zero disables waiting.

#### Scenario: Default wait equals polling interval
- **WHEN** no custom wait configuration is provided
- **THEN** the drain timeout matches the polling interval

#### Scenario: Multiplier scales wait time
- **WHEN** a multiplier of 2.0 is configured with a 5-second polling interval
- **THEN** the drain timeout is 10 seconds

#### Scenario: Zero multiplier disables waiting
- **WHEN** the wait multiplier is set to zero
- **THEN** the drain returns immediately if no data has already arrived
