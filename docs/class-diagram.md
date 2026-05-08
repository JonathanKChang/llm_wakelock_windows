# Class Architecture

```mermaid
classDiagram
    class TcpConnectionSource {
        <<Protocol>>
        +get_connections() list[dict]
    }

    class TcpConnectionMonitor {
        +ESTABLISHED = 0x01
        +MIB_TCP_STATE_ESTAB = 5
        +ES_CONTINUOUS = 0x80000000
        +ES_SYSTEM_REQUIRED = 0x00000001
        +AF_INET = 2
        +TCP_TABLE_OWNER_PID_ALL = 5
        +ERROR_INSUFFICIENT_BUFFER = 122
        +__init__(config)
        +is_monitored_active(connections, local_ports, remote_ports) bool
        +is_ssh_active(connections, local_ports, remote_ports, min_duration) bool
        +format_active_connections(connections, show_wsl_label) list[str]
        +_get_all_connections() list[dict]
        +has_active_connections() bool
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
        +__init__(config)
        +get_connections() list[dict]
    }

    class WslTcpConnectionHandler {
        <<abstract>>
        -_config dict
        -_command str
        -_process subprocess.Popen
        -_stdout_queue queue.Queue
        -_stdout_thread threading.Thread
        -_unavailable str | None
        +__init__(config, command)
        +_run_command(cmd) CompletedProcess
        +_stdout_reader(process)
        +_start_subprocess() subprocess.Popen
        +_subprocess_alive(process) bool
        +_drain_output() list[str]
        +_parse_proc_net_tcp_line(line) dict
        +_tcp_state_is_active(state_hex) bool
        +get_connections() list[dict]
        +unavailable str | None
    }

    class WslTcpHandler {
        +__init__(config)
        +_wsl_available() bool
        +get_connections() list[dict]
    }

    class WslDockerTcpHandler {
        -_container_id str
        +__init__(config, container_id)
        +get_connections() list[dict]
    }

    class WslDockerManager {
        -_config dict
        -_max_containers int
        -_container_handlers list[WslDockerTcpHandler]
        -_unavailable str | None
        +__init__(config)
        +_run_command(cmd) CompletedProcess
        +_discover_containers() list[str]
        +get_connections() list[dict]
        +unavailable str | None
    }

    TcpConnectionSource <|.. WindowsTcpHandler
    TcpConnectionSource <|.. WslTcpConnectionHandler
    TcpConnectionSource <|.. WslDockerManager

    WslTcpConnectionHandler <|-- WslTcpHandler
    WslTcpConnectionHandler <|-- WslDockerTcpHandler

    TcpConnectionMonitor --> WindowsTcpHandler : creates
    TcpConnectionMonitor --> WslTcpHandler : creates
    TcpConnectionMonitor --> WslDockerManager : creates conditionally

    WslDockerManager --> WslDockerTcpHandler : manages multiple
```

## Handler Hierarchy

```
TcpConnectionSource (Protocol)
├── WindowsTcpHandler          — Windows iphlpapi
├── WslTcpConnectionHandler    — abstract base for WSL subprocess
│   ├── WslTcpHandler          — /proc/net/tcp
│   └── WslDockerTcpHandler    — docker exec <container> /proc/net/tcp
└── WslDockerManager           — manages multiple WslDockerTcpHandler instances
```
