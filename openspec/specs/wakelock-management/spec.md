## Purpose

Manages a system wake lock that prevents the machine from entering sleep or hibernate while monitored network connections are active, with a configurable grace period after the last connection drops to allow ongoing operations to complete.

## Requirements

### Requirement: Wake lock acquisition
The system SHALL prevent sleep and hibernate by calling the platform's wake lock API with flags that hold the system awake. If the call fails, no exception is raised and the daemon continues running (at the risk of the system sleeping unexpectedly).

#### Scenario: Wakelock acquired successfully
- **WHEN** the wake lock acquire method is called
- **THEN** the system remains awake (does not enter sleep or hibernate)

#### Scenario: Acquire failure is handled gracefully
- **WHEN** the platform's wake lock API returns an error
- **THEN** no exception is raised and the daemon continues running normally

### Requirement: Wakelock release sequence
The system SHALL release the wake lock by first resetting the idle timer, waiting briefly, then clearing the wake lock flag. This two-step sequence ensures the idle timer is reset before release so that ongoing work is not interrupted.

#### Scenario: Wakelock released with proper reset
- **WHEN** the release method is called
- **THEN** the idle timer is reset, a short delay occurs, and then the wake lock is cleared
- **AND** the system's normal idle behavior resumes (system may sleep)

#### Scenario: Release is safe when no wakelock is held
- **WHEN** release is called while no wakelock is active
- **THEN** the full reset sequence executes without error or crash

### Requirement: Grace period before release
The system SHALL wait a configurable grace period (default: 30 minutes) after the last monitored connection drops before releasing the wake lock. If new connections appear during the grace period, the timer resets and the wakelock remains held.

#### Scenario: Wake lock persists after connection drop
- **WHEN** all monitored connections terminate
- **THEN** the wakelock remains held during the grace period

#### Scenario: Wakelock released after grace period elapses
- **WHEN** the configured grace period has passed since the last active connection
- **THEN** the wake lock is released

#### Scenario: New connection resets grace timer
- **WHEN** monitored connections reappear while the grace period is running
- **THEN** the grace timer resets and the wakelock stays held

#### Scenario: Custom grace period from configuration
- **WHEN** a custom grace period (e.g., 5 minutes) is configured
- **THEN** that shorter duration is used instead of the default

### Requirement: Wakelock state machine
The system SHALL manage wakelock state through four states, transitioning as follows:

```
(INACTIVE, no timer)
   │ active connections detected
   ▼
(ACTIVE, no timer) ───all connections drop──▶ (ACTIVE, grace timer running)
   ▲                                           │ grace period elapsed
   └──── new connections reset timer ◄──────────┘
```

#### Scenario: Inactive to active acquires wakelock
- **WHEN** the system is idle and monitored connections appear
- **THEN** the wakelock is acquired

#### Scenario: Active with no connections starts grace period
- **WHEN** the system holds a wakelock but all connections drop
- **THEN** the grace timer begins running

#### Scenario: Grace period elapse releases wakelock
- **WHEN** the grace timer expires while the wakelock is held
- **THEN** the wakelock is released and the system returns to the inactive state

### Requirement: Connection relevance determines wakelock state
A connection triggers wakelock acquisition only if it is "relevant": its port matches a monitored port list, or it is an SSH connection that has been active for at least the minimum duration. Non-relevant connections do not affect wakelock state.

#### Scenario: Monitored port triggers wakelock
- **WHEN** a connection exists on a monitored port
- **THEN** it is considered relevant and triggers wakelock acquisition

#### Scenario: Established SSH triggers wakelock
- **WHEN** an SSH connection has been active for at least the minimum duration
- **THEN** it is considered relevant and triggers wakelock acquisition

#### Scenario: New SSH connection does not trigger wakelock
- **WHEN** an SSH connection was just established and has not met the minimum duration
- **THEN** it is not considered relevant

#### Scenario: Non-monitored port does not affect state
- **WHEN** a connection uses a port that is neither monitored nor an SSH port
- **THEN** it does not contribute to wakelock state

### Requirement: Connection record schema
Every connection record returned by any source handler SHALL contain the following fields:
- `state` (integer): TCP connection state code
- `local_addr` (string): Local IPv4 address in dotted decimal notation
- `local_port` (integer): Local port number
- `remote_addr` (string): Remote IPv4 address in dotted decimal notation
- `remote_port` (integer): Remote port number
- `source` (enum): One of Windows, WSL2, or WSL_Docker

For Docker container connections only, the record MAY also contain:
- `container_id` (string): Short identifier for the container

#### Scenario: Windows connection has all required fields
- **WHEN** a connection is retrieved from the Windows source
- **THEN** it contains all six required fields with correct types
- **AND** it does not contain the optional `container_id` field

#### Scenario: Docker connection includes container ID
- **WHEN** a connection is retrieved from a Docker container handler
- **THEN** it contains all required fields plus the 12-character `container_id`
