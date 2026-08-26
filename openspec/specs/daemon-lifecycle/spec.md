## Purpose

Defines the lifecycle of a background daemon that monitors TCP connections on a polling cadence, manages its handlers based on configuration, handles signals for clean shutdown, and enforces platform constraints.

## Requirements

### Requirement: Fixed-interval polling
The daemon SHALL poll for connections at regular intervals, waiting only the remaining time after each poll completes so that the interval between polls is consistent. If a poll takes longer than the configured interval, the next poll begins immediately.

#### Scenario: Normal polling uses full interval
- **WHEN** a poll completes in 0.2 seconds with a 5-second interval
- **THEN** the daemon waits approximately 4.8 seconds before the next poll

#### Scenario: Poll exceeds interval
- **WHEN** a poll takes 6 seconds with a 5-second interval
- **THEN** the daemon begins the next poll immediately without waiting

### Requirement: Grace period counts during idle time
The system SHALL count elapsed time toward the grace period while the daemon is sleeping between polls, so that the full grace period must elapse before the wakelock releases.

#### Scenario: Sleep time counts toward grace period
- **WHEN** the daemon sleeps for multiple intervals after connections drop
- **THEN** each sleep interval advances the grace period timer

### Requirement: Handler creation based on configuration
The daemon SHALL create connection handlers based on its configuration flags. The Windows handler is always created. The WSL2 handler is created only when WSL2 monitoring is enabled. A Docker container manager is created only when Docker monitoring is configured with a positive maximum count.

#### Scenario: Minimal handler set
- **WHEN** WSL2 monitoring and Docker monitoring are both disabled
- **THEN** only the Windows connection handler is active

#### Scenario: WSL2 monitoring enabled
- **WHEN** WSL2 monitoring is enabled in configuration
- **THEN** both Windows and WSL2 handlers are created

#### Scenario: Docker monitoring enabled
- **WHEN** Docker container monitoring maximum is set to a positive value
- **THEN** the Docker container manager is created alongside other handlers

### Requirement: Clean shutdown on signal
The daemon SHALL register handlers for SIGINT and SIGTERM. Upon receiving either signal, it shall log a shutdown message, stop all connection handlers, and exit with code 0.

#### Scenario: SIGINT triggers clean shutdown
- **WHEN** the daemon receives SIGINT
- **THEN** it logs a shutdown message, stops all handlers, and exits with code 0

#### Scenario: SIGTERM triggers clean shutdown
- **WHEN** the daemon receives SIGTERM
- **THEN** it behaves identically to SIGINT

### Requirement: Error resilience in polling loop
The daemon SHALL never crash due to an exception during connection gathering. If a handler throws an error, the error is logged (when debug mode is enabled) and that handler's result for the current poll defaults to an empty list.

#### Scenario: Handler error does not crash daemon
- **WHEN** a connection handler raises an exception during polling
- **THEN** the daemon catches the error, logs it in debug mode, and continues with the next poll

### Requirement: Debug logging of all connections
When debug mode is enabled, the daemon SHALL print every TCP connection (not only active/monitored ones) at each poll cycle, prefixed with a source label indicating whether the connection came from Windows, WSL2, or a Docker container.

#### Scenario: Debug mode prints all connections
- **WHEN** debug mode is enabled and connections exist
- **THEN** all Windows, WSL2, and Docker connections are printed with their source labels

#### Scenario: Non-debug mode suppresses full connection list
- **WHEN** debug mode is disabled
- **THEN** only event-level messages (wakelock acquire/release, inactivity, reactivation) are logged, not the full connection list

### Requirement: Platform guard
The daemon SHALL verify that it is running on Windows at startup. If the current platform is not Windows, it shall print an error message to stderr and exit with a non-zero status code.

#### Scenario: Runs correctly on Windows
- **WHEN** the daemon starts on a Windows system
- **THEN** it proceeds normally and begins polling

#### Scenario: Exits on non-Windows platform
- **WHEN** the daemon runs on a non-Windows platform
- **THEN** it prints an error to stderr and exits with a non-zero code

### Requirement: Per-source port configuration
The daemon SHALL support per-source (Windows, WSL2, Docker) overrides for monitored ports and SSH ports. Each source uses its own override when available, falling back to global defaults for missing fields or absent sections.

#### Scenario: Global config applies to all sources
- **WHEN** no per-source overrides are defined
- **THEN** all sources use the same port lists from global configuration

#### Scenario: One source overrides globally
- **WHEN** a single source defines its own monitored ports
- **THEN** that source matches only its configured ports while other sources use the global defaults

#### Scenario: Partial per-source override
- **WHEN** a per-source section defines monitored ports but not SSH ports
- **THEN** the source uses the override for ports and falls back to global defaults for SSH
