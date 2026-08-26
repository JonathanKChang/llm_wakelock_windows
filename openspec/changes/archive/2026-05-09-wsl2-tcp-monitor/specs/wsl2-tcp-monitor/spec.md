## ADDED Requirements

### Requirement: WSL2 monitoring toggle
The system SHALL support an `enable_wsl2_monitoring` configuration option that controls whether WSL2 connections are monitored.

#### Scenario: WSL2 monitoring enabled
- **WHEN** `enable_wsl2_monitoring` is set to `true` in config.toml and WSL2 is available
- **THEN** the monitor queries WSL2 TCP connections on each poll cycle
- **AND** the existing `local_monitored_ports`, `remote_monitored_ports`, `local_ssh_ports`, and `remote_ssh_ports` apply to WSL2 connections in addition to Windows connections

#### Scenario: WSL2 monitoring disabled
- **WHEN** `enable_wsl2_monitoring` is set to `false` or is not set in config.toml
- **THEN** the monitor does not query WSL2 connections

#### Scenario: WSL2 monitoring enabled but WSL2 unavailable
- **WHEN** `enable_wsl2_monitoring` is `true` but WSL2 is not installed, not running, or `wsl.exe` is not found
- **THEN** the monitor logs a warning once and skips WSL2 monitoring without crashing
- **AND** Windows-only monitoring continues normally using the existing port/SSH config

#### Scenario: WSL2 becomes temporarily unavailable
- **WHEN** WSL2 becomes unavailable (e.g., WSL shutting down) during a poll
- **THEN** the monitor logs a warning and skips WSL2 monitoring for that cycle
- **AND** resumes WSL2 monitoring on the next poll cycle when WSL2 is available again

### Requirement: Persistent WSL2 subprocess for TCP monitoring
The system SHALL maintain a persistent subprocess inside WSL2 that reads `/proc/net/tcp` and writes one line per connection to stdout.

#### Scenario: Subprocess starts successfully
- **WHEN** the monitor starts, `enable_wsl2_monitoring` is `true`, and WSL2 is available
- **THEN** the system spawns a persistent `wsl.exe -e bash ~/bin/wsl2_tcp_monitor.sh` subprocess
- **AND** the subprocess reads `/proc/net/tcp` and writes connection lines to stdout

#### Scenario: Subprocess dies
- **WHEN** the subprocess exits (crash, WSL restart, or pipe EOF)
- **THEN** the system detects the death on the next poll cycle
- **AND** automatically restarts a new subprocess

### Requirement: TCP connection parsing in Python
The system SHALL parse `/proc/net/tcp` output lines in Python, extracting local port, remote port, and TCP state.

#### Scenario: Parse established connection
- **WHEN** a line like `0: 00000000:1F90 0500000A:1F40 0A ...` is received
- **THEN** the system extracts local_port = 8080 (0x1F90), remote_port = 8000 (0x1F40), state = ESTABLISHED (0x0A)

#### Scenario: Parse header line
- **WHEN** the first line of `/proc/net/tcp` output (containing "local_address") is received
- **THEN** the system skips it and does not treat it as a connection

#### Scenario: Parse malformed line
- **WHEN** a line that does not match the expected format is received
- **THEN** the system logs a warning and skips the line without crashing

### Requirement: WSL2 helper script deployment
The system SHALL deploy a bash helper script to WSL2 on first startup if not already present.

#### Scenario: Helper script not present
- **WHEN** the monitor starts, `enable_wsl2_monitoring` is `true`, and the helper script is not found in WSL2
- **THEN** the monitor creates `~/bin/wsl2_tcp_monitor.sh` inside WSL2 via `wsl.exe`
- **AND** the script contains `cat /proc/net/tcp`

#### Scenario: Helper script already present
- **WHEN** the monitor starts, `enable_wsl2_monitoring` is `true`, and `~/bin/wsl2_tcp_monitor.sh` already exists in WSL2
- **THEN** the monitor uses the existing script without modification

### Requirement: Shared port/SSH config for Windows and WSL2
The system SHALL use the same port and SSH configuration for both Windows and WSL2 connections when WSL2 monitoring is enabled.

#### Scenario: Port match on WSL2 connection
- **WHEN** a WSL2 connection's local or remote port matches a port in `local_monitored_ports` or `remote_monitored_ports`
- **THEN** the connection counts as active and contributes to wakelock acquisition

#### Scenario: SSH match on WSL2 connection
- **WHEN** a WSL2 connection's local or remote port matches a port in `local_ssh_ports` or `remote_ssh_ports`
- **THEN** the connection is tracked for SSH duration and contributes to wakelock after `ssh_min_duration`

#### Scenario: No WSL2 ports configured
- **WHEN** the existing port lists are empty and WSL2 monitoring is enabled
- **THEN** WSL2 connections are queried but none match, so they do not trigger wakelock
