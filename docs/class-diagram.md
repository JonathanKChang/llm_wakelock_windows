# Class Architecture

```mermaid
classDiagram
    class TcpConnectionSource {
        <<Protocol>>
        +get_connections() list[dict]
        +cleanup() None
        +_stopped bool
    }

    class SubprocessDrain {
        -_process subprocess.Popen
        -_queue Queue[str]
        -_thread Thread
        -_sentinel str
        -_full_command str
        -_stop_timeout int
        -_interval float
        -_max_consecutive_failures int
        -_recovery_interval int
        -_consecutive_failures int
        -_last_restart_attempt float
        -_death_warned bool
        -_stopped bool
        -_last_output list[str]
        -_owner str
        +__init__(command, config)
        +start() subprocess.Popen | None
        +drain(timeout) list[str]
        +alive bool
        +stop() None
        +restart() None
    }

    class TcpConnectionMonitor {
        -_config dict
        -_handlers list[TcpConnectionSource]
        -_ssh_start_times dict
        +__init__(config)
        +is_monitored_active(connections, local_ports, remote_ports) bool
        +is_ssh_active(connections, local_ports, remote_ports, min_duration) bool
        +format_connections(connections, show_source_label) list[str]
        +get_all_connections() list[dict]
        +has_active_connections(connections, config) bool
        +_acquire()
        +_release()
        +run()
    }

    class ConnectionSource {
        <<Enum>>
        WINDOWS = 0
        WSL = 1
        WSL_DOCKER = 2
    }

    class WindowsTcpHandler {
        -_config dict
        -_debug bool
        +__init__(config)
        +get_connections() list[dict]
        +cleanup() None
    }

    class WslTcpConnectionHandler {
        <<abstract>>
        -_config dict
        -_drain SubprocessDrain
        -_stopped bool
        -_debug bool
        +__init__(config, command)
        +_parse_proc_net_tcp_line(line) dict | None
        +_tcp_state_is_active(state_hex) bool
        +get_connections() list[dict]
        +cleanup() None
    }

    class WslTcpHandler {
        +__init__(config)
        +get_connections() list[dict]
    }

    class WslDockerTcpHandler {
        -_container_id str
        +__init__(config, container_id)
        +get_connections() list[dict]
    }

    class WslDockerManager {
        -_config dict
        -_stopped bool
        -_handlers dict[str, WslDockerTcpHandler]
        -_drain SubprocessDrain
        -_last_discovery_time float
        +__init__(config)
        +_discover() None
        +get_connections() list[dict]
        +cleanup() None
    }

    TcpConnectionSource <|.. WindowsTcpHandler
    TcpConnectionSource <|.. WslTcpConnectionHandler
    TcpConnectionSource <|.. WslDockerManager

    WslTcpConnectionHandler <|-- WslTcpHandler
    WslTcpConnectionHandler <|-- WslDockerTcpHandler

    WslTcpConnectionHandler --> SubprocessDrain : uses
    WslDockerManager --> SubprocessDrain : uses for discovery

    TcpConnectionMonitor --> WindowsTcpHandler : creates
    TcpConnectionMonitor --> WslTcpHandler : creates
    TcpConnectionMonitor --> WslDockerManager : creates conditionally

    WslDockerManager --> WslDockerTcpHandler : manages by dict key
```

## Handler Hierarchy

```
TcpConnectionSource (Protocol)
├── WindowsTcpHandler          — Windows iphlpapi
├── WslTcpConnectionHandler    — WSL subprocess via SubprocessDrain
│   ├── WslTcpHandler          — /proc/net/tcp
│   └── WslDockerTcpHandler    — docker exec <container> /proc/net/tcp
└── WslDockerManager           — dict-based handler tracking + persistent discovery
    └── SubprocessDrain        — shared: loop + sentinel + drain thread + queue
```

## SubprocessDrain Lifecycle

`SubprocessDrain` owns its own lifecycle. When `drain()` detects process death (via `poll() != None`) or repeated sentinel misses, it automatically calls `stop()` then `restart()` (after cooldown), resetting `_consecutive_failures`. No exceptions propagate to handlers — they simply receive cached output or `[]`.

```
drain() ──► process.poll() != None ──► log death warning ──► _restart_if_needed()
            │
            └── sentinel miss ──► increment failures ──► _restart_if_needed()

_restart_if_needed() ──► cooldown elapsed? ──► max failures met? ──► restart()
restart() ──► stop() ──► sleep(0.5s) ──► start() ──► reset failures
```
