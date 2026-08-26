## MODIFIED Requirements

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
