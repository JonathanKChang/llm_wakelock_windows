## Purpose

Enables monitoring of TCP connections inside WSL2 by deploying and maintaining a persistent helper process, parsing raw TCP state data, and providing automatic recovery when WSL2 becomes temporarily unavailable.

## Requirements

### Requirement: WSL2 monitoring toggle
The system SHALL support an on/off configuration that controls whether WSL2 connections are monitored. When enabled but WSL2 is unavailable (not installed, not running, or the executable is missing), the system logs a warning and continues retrying on each poll cycle without crashing. Windows-only monitoring proceeds unaffected.

#### Scenario: WSL2 monitoring enabled and available
- **WHEN** the toggle is true and WSL2 is present
- **THEN** the monitor queries WSL2 TCP connections on each poll cycle

#### Scenario: WSL2 monitoring disabled
- **WHEN** the toggle is false or absent
- **THEN** no WSL2 connections are queried

#### Scenario: WSL2 unavailable logs warning
- **WHEN** the toggle is true but WSL2 cannot be reached
- **THEN** a warning is logged and the monitor continues retrying on subsequent polls

#### Scenario: Temporary unavailability recovers
- **WHEN** WSL2 was available, becomes unavailable, then comes back
- **THEN** the monitor detects the disruption, logs a warning, and resumes monitoring after recovery

### Requirement: Persistent TCP state subprocess
The system SHALL maintain a long-running process inside WSL2 that reads the kernel's TCP connection table and emits one line per connection to its output stream. This process survives across polls.

#### Scenario: Subprocess starts on initialization
- **WHEN** WSL2 monitoring is enabled and WSL2 is available
- **THEN** a persistent subprocess is spawned inside WSL2

#### Scenario: Subprocess dies and recovers
- **WHEN** the subprocess terminates unexpectedly
- **THEN** the system detects the death and spawns a replacement on the next poll

### Requirement: TCP state parsing
The system SHALL parse the kernel TCP connection table output, extracting local port, remote port, and connection state for each line. It SHALL skip header lines and gracefully handle malformed entries by logging a warning and continuing.

#### Scenario: Valid connection line is parsed correctly
- **WHEN** a well-formed TCP state line is received
- **THEN** the local port, remote port, and connection state are extracted accurately

#### Scenario: Header line is skipped
- **WHEN** the output contains a header row
- **THEN** it is ignored and does not produce a connection record

#### Scenario: Malformed line is handled gracefully
- **WHEN** a line does not match the expected format
- **THEN** a warning is logged and parsing continues with remaining lines

### Requirement: Helper script deployment
On first startup, the system SHALL deploy a helper script into WSL2 if one is not already present. If a script already exists, it is used as-is without modification.

#### Scenario: Script deployed on first run
- **WHEN** the helper script does not exist in WSL2
- **THEN** the system creates it before starting the subprocess

#### Scenario: Existing script is reused
- **WHEN** the helper script already exists in WSL2
- **THEN** no new script is created; the existing one is used

### Requirement: Shared port and SSH configuration
When WSL2 monitoring is enabled, WSL2 connections use the same monitored port lists and SSH port lists as Windows connections. If no ports are configured, WSL2 connections are queried but produce no wakelock-triggering matches.

#### Scenario: WSL2 port match triggers wakelock
- **WHEN** a WSL2 connection's local or remote port matches a configured monitored port
- **THEN** the connection counts as active for wakelock purposes

#### Scenario: WSL2 SSH match tracks session
- **WHEN** a WSL2 connection matches an SSH port
- **THEN** it is tracked for minimum duration and contributes to wakelock after the threshold

#### Scenario: No ports configured
- **WHEN** monitored port lists are empty
- **THEN** WSL2 connections are collected but do not trigger wakelock acquisition

### Requirement: Subprocess auto-restart with cooldown
When a subprocess dies or fails to produce valid output for N consecutive attempts (where N is the failure threshold), the system SHALL restart it after a configurable cooldown interval. Upon successful recovery, an info-level log is emitted. Silent accumulation of failures does not produce per-failure log spam.

#### Scenario: Process death logs warning
- **WHEN** the subprocess terminates
- **THEN** a warning is logged with the owner name and cooldown value

#### Scenario: Successful restart logs info
- **WHEN** a restarted subprocess begins producing valid output
- **THEN** an informational message confirms the restart

#### scenario: Fresh data after recovery logs re-established
- **WHEN** new connection data arrives after a recovery
- **THEN** an informational message indicates the connection stream was re-established

#### Scenario: Silent failure counting
- **WHEN** the subprocess produces output without expected delimiters
- **THEN** the failure counter increments silently without logging each miss

#### Scenario: Cooldown prevents rapid retries
- **WHEN** a restart attempt fails
- **THEN** the next attempt waits at least the configured cooldown interval

### Requirement: Unified recovery interval
The system SHALL use a single configuration parameter for both subprocess restart cooldown and Docker container discovery interval, eliminating the need for separate timing settings. The default value is 60 seconds.

#### Scenario: Default recovery interval
- **WHEN** no custom interval is configured
- **THEN** the default of 60 seconds is used for both subprocess restart cooldown and discovery

#### Scenario: Custom interval applies to both
- **WHEN** a custom interval (e.g., 120 seconds) is configured
- **THEN** both subprocess recovery and container discovery use that value
