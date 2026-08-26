## Requirements

### Requirement: WSL2 monitoring toggle
The system SHALL support an `enable_wsl2_monitoring` configuration option that controls whether WSL2 connections are monitored. When WSL2 is not installed, not running, or `wsl.exe` is not found and monitoring is enabled, the system SHALL log a warning with timestamp and owner name, then continue retrying on each poll cycle without crashing. Windows-only monitoring continues normally using the existing port/SSH config.

#### Scenario: WSL2 monitoring enabled
- **WHEN** `enable_wsl2_monitoring` is set to `true` in config.toml and WSL2 is available
- **THEN** the monitor queries WSL2 TCP connections on each poll cycle
- **AND** the existing `local_monitored_ports`, `remote_monitored_ports`, `local_ssh_ports`, and `remote_ssh_ports` apply to WSL2 connections in addition to Windows connections

#### Scenario: WSL2 monitoring disabled
- **WHEN** `enable_wsl2_monitoring` is set to `false` or is not set in config.toml
- **THEN** the monitor does not query WSL2 connections

#### Scenario: WSL2 monitoring enabled but WSL2 unavailable logs warning
- **WHEN** `enable_wsl2_monitoring` is `true` but WSL2 is not installed, not running, or `wsl.exe` is not found
- **THEN** the monitor logs a warning once with timestamp and handler owner name, then continues retrying on each poll cycle

#### Scenario: WSL2 becomes temporarily unavailable recovers automatically
- **WHEN** WSL2 becomes unavailable (e.g., WSL shutting down) during a poll
- **THEN** the monitor detects the subprocess death, logs a warning with timestamp, and automatically restarts the subprocess when `wsl_recovery_interval` cooldown has elapsed

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

### Requirement: Subprocess auto-restart with cooldown and logging
The `SubprocessDrain` class SHALL manage its own subprocess lifecycle including automatic restart on process death or sentinel failure threshold. When the subprocess dies (detected via `poll() != None`), `SubprocessDrain` SHALL log a warning with an ISO timestamp, owner name, and the `wsl_recovery_interval` cooldown value. After `max_consecutive_failures` sentinel misses (default: 10) or process death, and after at least `wsl_recovery_interval` seconds have elapsed since the last restart attempt, `SubprocessDrain` SHALL restart the subprocess (terminate old, spawn new) and reset the consecutive failure counter on successful start. On successful recovery producing fresh data, `SubprocessDrain` SHALL log an info message with timestamp and owner name. The owner string is injected at construction time (e.g., "WSL /proc/net/tcp", "Docker container abc123").

#### Scenario: Process death logs warning with timestamp and owner
- **WHEN** the subprocess process has terminated
- **THEN** `SubprocessDrain` logs a warning: `[YYYY-MM-DD HH:MM:SS] [WARN] <owner> process died — retrying in 60s`

#### Scenario: Successful restart logs info message
- **WHEN** the subprocess terminates and a new subprocess successfully starts
- **THEN** `SubprocessDrain` logs an info message: `[YYYY-MM-DD HH:MM:SS] [INFO] <owner> restarted successfully`

#### Scenario: Fresh data after recovery logs re-established
- **WHEN** the subprocess has been restarted and the next successful drain finds a sentinel pair
- **THEN** `SubprocessDrain` logs an info message: `[YYYY-MM-DD HH:MM:SS] [INFO] <owner> re-established`

#### Scenario: Sentinel misses accumulate without spamming logs
- **WHEN** the subprocess is alive but failing to produce sentinel markers (e.g., WSL load, slow commands)
- **THEN** `SubprocessDrain` increments the consecutive failure counter silently until the threshold triggers restart; no per-miss logging

#### Scenario: Restart cooldown prevents rapid retries
- **WHEN** a subprocess restart fails (WSL still unavailable)
- **THEN** the next restart attempt waits at least `wsl_recovery_interval` seconds before retrying

### Requirement: Discovery and recovery interval config
The system SHALL use a `wsl_recovery_interval` configuration parameter (default: 60 seconds, replaces the previous `wsl_docker_discovery_interval`) to control both the subprocess restart cooldown in `SubprocessDrain` and the Docker container discovery cadence in `WslDockerManager`.

#### Scenario: Default interval is 60 seconds
- **WHEN** `wsl_recovery_interval` is not specified in config
- **THEN** the default value is 60 seconds

#### Scenario: Discovery uses same interval as restart cooldown
- **WHEN** `WslDockerManager` determines when to run docker ps discovery
- **THEN** it uses `wsl_recovery_interval` as the time between discovery cycles
