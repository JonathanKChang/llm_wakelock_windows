## Purpose

Provides a unified interface for gathering and analyzing TCP connections across Windows and WSL2 environments, with utilities to detect monitored ports, track SSH sessions, and format connection data consistently.

## Requirements

### Requirement: Connection source abstraction
The system SHALL define a common interface for retrieving TCP connections from different platforms. Each source returns connection records containing state, local address, local port, remote address, remote port, and a source identifier distinguishing Windows from WSL2.

#### Scenario: Windows source returns Windows-tagged connections
- **WHEN** the Windows connection source is queried
- **THEN** each returned record has the source identifier set to Windows

#### Scenario: WSL2 source returns WSL2-tagged connections
- **WHEN** the WSL2 connection source is queried
- **THEN** each returned record has the source identifier set to WSL2

### Requirement: Monitored port detection
The system SHALL determine whether any active connection matches a provided list of monitored ports, checking both local and remote ports against those lists.

#### Scenario: Local port match is detected
- **WHEN** a connection's local port appears in the monitored list
- **THEN** the system reports that a monitored connection is active

#### Scenario: Remote port match is detected
- **WHEN** a connection's remote port appears in the monitored list
- **THEN** the system reports that a monitored connection is active

#### Scenario: No monitored ports match
- **WHEN** no connection has a local or remote port in the monitored lists
- **THEN** the system reports that no monitored connections are active

### Requirement: SSH session tracking
The system SHALL track SSH sessions by their port and remote address, detecting when a session has been active long enough to be considered established, and pruning stale entries when connections drop.

#### Scenario: Established SSH connection is reported
- **WHEN** an SSH session has been active for at least the minimum duration
- **THEN** the system reports that SSH is active

#### Scenario: New SSH connection is not yet established
- **WHEN** an SSH session was just established and has not yet met the minimum duration
- **THEN** the system reports that SSH is not active

#### Scenario: Stale session entry is cleaned up
- **WHEN** an SSH connection terminates and no longer appears in the connection list
- **THEN** its tracking entry is removed

### Requirement: Connection formatting
The system SHALL format a list of connection records into human-readable strings, with each connection showing the local and remote address pairs, optionally prefixed with a source label.

#### Scenario: Windows connections labeled
- **WHEN** a Windows connection is formatted with source labels
- **THEN** the output is prefixed with "[win]"

#### Scenario: WSL2 connections labeled
- **WHEN** a WSL2 connection is formatted with source labels
- **THEN** the output is prefixed with "[wsl2]"

#### Scenario: No source label
- **WHEN** connections are formatted without source labels
- **THEN** no prefix is added to the output

### Requirement: Subprocess auto-recovery
The system SHALL continue monitoring even when a platform subprocess terminates (due to shutdown, crash, or pipe closure), by delegating recovery to an internal manager that automatically restarts the subprocess.

#### Scenario: Monitoring survives subprocess death
- **WHEN** a WSL2 subprocess terminates unexpectedly
- **THEN** the handler does not permanently stop; it continues monitoring after the subprocess recovers

#### Scenario: Docker container handlers recover independently
- **WHEN** one Docker container exits or its monitoring subprocess dies
- **THEN** that container's handler recovers independently while other containers continue being monitored
