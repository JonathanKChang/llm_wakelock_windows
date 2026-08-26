## Requirements

### Requirement: TcpConnectionSource protocol
The system SHALL define a `TcpConnectionSource` Protocol with a `get_connections() -> list[dict]` method. Both Windows and WSL2 connection sources SHALL implement this Protocol. Each connection dict MUST contain: `state`, `local_addr`, `local_port`, `remote_addr`, `remote_port`, `is_wsl2`.

#### Scenario: Windows handler implements the protocol
- **WHEN** `WindowsTcpHandler` is instantiated and `get_connections()` is called
- **THEN** it returns a list of dicts with `is_wsl2=False` for each established TCP connection

#### Scenario: WSL2 handler implements the protocol
- **WHEN** `Wsl2TcpHandler` is instantiated and `get_connections()` is called
- **THEN** it returns a list of dicts with `is_wsl2=True` for each established TCP connection

### Requirement: Unified monitored connection detection
The system SHALL have a single `is_monitored_active(connections, local_ports, remote_ports)` function that checks if any connection in a list has a local or remote port matching the monitored port lists. It SHALL work with connection dicts from any source.

#### Scenario: Monitored local port detected
- **WHEN** a connection has `local_port` in the monitored list
- **THEN** `is_monitored_active` returns `True`

#### Scenario: Monitored remote port detected
- **WHEN** a connection has `remote_port` in the monitored list
- **THEN** `is_monitored_active` returns `True`

#### Scenario: No monitored ports match
- **WHEN** no connection has a matching local or remote port
- **THEN** `is_monitored_active` returns `False`

### Requirement: Unified SSH active detection
The system SHALL have a single `is_ssh_active(connections, ssh_start_times, local_ssh_ports, remote_ssh_ports, min_duration)` function that tracks SSH session durations. The SSH key SHALL be `(local_port, remote_port, remote_addr)` — no PID. Stale entries are pruned when connections drop.

#### Scenario: SSH session exceeds minimum duration
- **WHEN** an SSH connection has been tracked for at least `min_duration` seconds
- **THEN** `is_ssh_active` returns `True`

#### Scenario: SSH session below minimum duration
- **WHEN** an SSH connection was recently established (less than `min_duration` seconds)
- **THEN** `is_ssh_active` returns `False`

#### Scenario: Stale SSH entry is pruned
- **WHEN** an SSH connection drops and no longer appears in the connection list
- **THEN** its tracking entry is removed from `ssh_start_times`

#### Scenario: Reconnected SSH with same ports resets timer
- **WHEN** a previous SSH key was pruned and a new connection with the same ports appears
- **THEN** the timer starts fresh (treated as new session)

### Requirement: Unified connection formatting for logging
The system SHALL have a single `format_active_connections(connections, show_wsl2_label=True)` function that formats a list of active connection dicts into human-readable strings for logging. When `show_wsl2_label` is True, connections with `is_wsl2=True` are prefixed with `[wsl2]` and others with `[win]`.

#### Scenario: Format Windows connections with labels
- **WHEN** `format_active_connections` is called with Windows connections and `show_wsl2_label=True`
- **THEN** each connection is prefixed with `[win]`

#### Scenario: Format WSL2 connections with labels
- **WHEN** `format_active_connections` is called with WSL2 connections and `show_wsl2_label=True`
- **THEN** each connection is prefixed with `[wsl2]`

#### Scenario: Format without labels
- **WHEN** `show_wsl2_label=False`
- **THEN** no source prefix is added

### Requirement: Handler encapsulation
The system SHALL encapsulate all Windows TCP table retrieval in a `WindowsTcpHandler` class and all WSL2 TCP polling in a `WslTcpHandler` class. The main loop SHALL call `handler.get_connections()` through the shared Protocol interface. When a WSL subprocess dies (WSL shutdown, crash, or pipe EOF), the handler SHALL NOT permanently stop monitoring; instead it SHALL continue calling `SubprocessDrain.drain()` which manages subprocess auto-restart internally. Handlers SHALL pass an owner string to SubprocessDrain for contextual logging (e.g., "WSL /proc/net/tcp").

#### Scenario: Main loop uses handler interface
- **WHEN** the main loop needs connections
- **THEN** it calls `windows_handler.get_connections()` and optionally `wsl2_handler.get_connections()` if enabled

#### Scenario: WSL2 handler is disabled
- **WHEN** `enable_wsl2_monitoring` is `False`
- **THEN** `Wsl2TcpHandler` is not instantiated and no WSL2 subprocess is spawned

#### Scenario: Handler continues monitoring after subprocess dies
- **WHEN** a WSL subprocess terminates (e.g., `wsl --shutdown`)
- **THEN** the handler does not permanently stop; it continues calling `drain()` which auto-restarts the subprocess

#### Scenario: Docker container handlers recover independently
- **WHEN** a Docker container exits or its subprocess dies
- **THEN** the individual `WslDockerTcpHandler` for that container auto-recovers via SubprocessDrain; the `WslDockerManager` continues monitoring other containers
