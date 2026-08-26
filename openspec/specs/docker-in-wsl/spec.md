## Purpose

Enables automatic discovery and monitoring of Docker container network traffic through WSL2, managing per-container subprocesses that read container TCP state and aggregating connections into the unified connection interface.

## Requirements

### Requirement: Container discovery via periodic polling
The system SHALL discover running Docker containers by periodically querying the Docker daemon for a list of active container IDs. Discovery runs on a configurable interval, not on every connection poll.

#### Scenario: Discovery runs on configured interval
- **WHEN** the configured discovery interval has elapsed
- **THEN** the system queries Docker for the current list of running container IDs

#### Scenario: No containers returns empty
- **WHEN** the Docker daemon reports no running containers
- **THEN** all previously tracked container handlers are cleaned up

### Requirement: New container tracking
When a newly discovered container appears, the system SHALL create a monitoring handler for it, up to a configurable maximum container count. Containers beyond the cap are silently ignored.

#### Scenario: New container starts being monitored
- **WHEN** `docker ps` returns a container ID not previously tracked
- **THEN** a monitoring handler is created and begins collecting that container's connections

#### Scenario: Container exited stops being monitored
- **WHEN** a previously tracked container no longer appears in `docker ps` output
- **THEN** its monitoring handler is stopped and removed

#### Scenario: Container count capped
- **WHEN** more containers are running than the configured maximum
- **THEN** only the first N containers are monitored; additional ones are ignored

#### Scenario: Container restart gets new handler
- **WHEN** a container stops and starts again with a new container ID
- **THEN** the old handler is cleaned up and a new one is created for the new ID

### Requirement: Per-container TCP monitoring subprocess
Each tracked container SHALL have a persistent subprocess running inside it that reads the container's TCP connection state. The subprocess uses an internal manager for lifecycle handling, providing automatic restart on failure and cached output during recovery.

#### Scenario: Subprocess starts on handler creation
- **WHEN** a container monitoring handler is created
- **THEN** it spawns a subprocess inside the container to read TCP connection data

#### Scenario: Subprocess recovers after failure
- **WHEN** a container's subprocess terminates unexpectedly
- **THEN** the manager detects the death and restarts the subprocess after a cooldown period, returning cached output during recovery

### Requirement: Docker connection enrichment
Every connection returned by a container monitoring handler SHALL include a source identifier marking it as originating from a Docker container, and a short container ID for identification.

#### Scenario: Connection has correct source marker
- **WHEN** connections are retrieved from a Docker container handler
- **THEN** each connection is marked as Docker-sourced

#### Scenario: Connection includes container ID
- **WHEN** connections are retrieved from a Docker container handler
- **THEN** each connection includes the short identifier of its container

### Requirement: Discovery subprocess lifecycle
The system SHALL maintain a dedicated subprocess for container discovery that runs continuously with automatic recovery. When the manager is shut down, both the discovery subprocess and all container monitoring subprocesses are terminated.

#### Scenario: Discovery subprocess starts with manager
- **WHEN** the Docker manager initializes
- **THEN** it immediately performs one discovery cycle to bootstrap

#### Scenario: Cleanup stops all processes
- **WHEN** the Docker manager is shut down
- **THEN** all container monitoring subprocesses and the discovery subprocess are terminated

### Requirement: Timer-based discovery, not per-poll
Container discovery runs on a timer independent of connection polls. On the first poll, discovery runs immediately to initialize tracking. Subsequent discoveries only occur when the configured interval has elapsed since the last discovery.

#### Scenario: First discovery runs immediately
- **WHEN** the manager is freshly created and `get_connections()` is called
- **THEN** an initial discovery runs to populate the handler list

#### Scenario: Discovery does not run on every poll
- **WHEN** less than the configured interval has passed since the last discovery
- **THEN** only existing container handlers are polled for connections; no new discovery occurs
