# Component Architecture

```mermaid
graph TB
    subgraph Application["llm_wakelock_windows.py (Daemon)"]
        direction TB
        Main["<b>main()</b><br/>Entry point"]:::entry
        ConfigLoader["<b>load_config()</b><br/>Merge defaults + config.toml"]:::core
        Monitor["<b>TcpConnectionMonitor</b><br/>Core orchestrator<br/>• Port matching<br/>• SSH duration tracking<br/>• Wakelock state machine"]:::core

        subgraph Handlers["Connection Handlers (TcpConnectionSource)"]
            direction TB
            WinHandler["<b>WindowsTcpHandler</b><br/>iphlpapi.GetExtendedTcpTable"]:::handler
            WslHandler["<b>WslTcpHandler</b><br/>/proc/net/tcp via WSL<br/>SubprocessDrain"]:::handler
            DockerMgr["<b>WslDockerManager</b><br/>Persistent discovery subprocess<br/>Dict-based handler tracking<br/>→ WslDockerTcpHandler × N"]:::handler
        end

        subgraph SubprocessDrain["SubprocessDrain (Shared)"]
            direction LR
            DrainLoop["<b>drain loop</b><br/>echo SENTINEL<br/>while true; do<br/>  &lt;command&gt;;<br/>  echo SENTINEL;<br/>  sleep N; done"]:::drain
            DrainQueue["<b>Queue</b><br/>Bounded (max 1000 lines)<br/>Daemon reader thread"]:::drain
            DrainLogic["<b>drain()</b><br/>queue.get(timeout=remaining)<br/>Scans last 2 sentinels<br/>Returns lines between them"]:::drain
        end

        subgraph Wakelock["Wakelock Engine"]
            Acquire["<b>_acquire()</b><br/>SetThreadExecutionState<br/>ES_SYSTEM_REQUIRED"]:::wakelock
            Release["<b>_release()</b><br/>SetThreadExecutionState<br/>ES_CONTINUOUS"]:::wakelock
        end
    end

    subgraph Configuration["Configuration"]
        ConfigFile["<b>config.toml</b><br/>• local/remote monitored ports<br/>• SSH port config<br/>• polling_interval<br/>• wsl_monitoring<br/>• wsl_docker_monitoring_max<br/>• wsl_command_timeout<br/>• wsl_recovery_interval"]:::config
        Defaults["<b>DEFAULTS</b><br/>Built-in fallback values"]:::config
    end

    subgraph OS_Interfaces["OS / External Interfaces"]
        direction TB
        Iphlpapi["<b>iphlpapi.dll</b><br/>GetExtendedTcpTable<br/>Windows TCP table"]:::os
        Kernel32["<b>kernel32.dll</b><br/>SetThreadExecutionState<br/>Wake lock API"]:::os
        WSL["<b>WSL /proc/net/tcp</b><br/>Persistent subprocess<br/>stdout → Queue → Sentinel-based drain"]:::os
        Docker["<b>Docker in WSL</b><br/>docker ps (discovery loop)<br/>docker exec (per-container polling)"]:::os
    end

    subgraph Utilities["Utilities & Tests"]
        DumpTool["<b>dump_iphlpapi.py</b><br/>Raw TCP table binary dump"]:::util
        WslScript["<b>wsl_tcp_monitor.sh</b><br/>WSL /proc/net/tcp reader"]:::util
        Tests["<b>tests/test_wakelock.py</b><br/>SSH tracking, parsing,<br/>port matching, Docker"]:::util
    end

    %% Configuration flow
    ConfigFile --> Defaults
    Defaults --> ConfigLoader
    ConfigLoader --> Monitor
    ConfigLoader -.-> Defaults

    %% Main flow
    ConfigLoader --> Main
    Main --> Monitor

    %% Monitor → Handlers
    Monitor --> WinHandler
    Monitor -. wsl_monitoring .-> WslHandler
    Monitor -. wsl_docker_monitoring_max .-> DockerMgr

    %% Handlers → OS
    WinHandler --> Iphlpapi
    WslHandler --> WSL
    DockerMgr --> Docker

    %% Monitor → Wakelock
    Monitor --> Acquire
    Monitor --> Release
    Acquire --> Kernel32
    Release --> Kernel32

    %% SubprocessDrain used by handlers
    WslHandler --> DrainLoop
    WslHandler --> DrainQueue
    WslHandler --> DrainLogic
    DockerMgr --> DrainLoop
    DockerMgr --> DrainQueue
    DockerMgr --> DrainLogic

    %% Utilities
    DumpTool --> Iphlpapi
    WslScript -.-> WSL

    classDef entry fill:#1a5276,stroke:#1a5276,color:#fff,font-weight:bold
    classDef core fill:#2e86c1,stroke:#2e86c1,color:#fff
    classDef handler fill:#3498db,stroke:#3498db,color:#fff
    classDef wakelock fill:#e74c3c,stroke:#e74c3c,color:#fff
    classDef config fill:#8e44ad,stroke:#8e44ad,color:#fff
    classDef os fill:#7f8c8d,stroke:#7f8c8d,color:#fff
    classDef util fill:#95a5a6,stroke:#95a5a6,color:#fff
    classDef drain fill:#16a085,stroke:#16a085,color:#fff
```

## Component Responsibilities

| Component | Layer | Responsibility |
|---|---|---|
| **main()** | Entry | Bootstrap: load config → create monitor → run loop |
| **load_config()** | Configuration | Merge `config.toml` overrides with `DEFAULTS` |
| **TcpConnectionMonitor** | Core | Orchestrates handlers, matches ports, tracks SSH duration, manages wakelock state |
| **WindowsTcpHandler** | Handler | Reads Windows TCP table via `iphlpapi.GetExtendedTcpTable` |
| **WslTcpHandler** | Handler | Reads WSL `/proc/net/tcp` via `SubprocessDrain` (persistent subprocess + queue + sentinel drain) |
| **WslDockerManager** | Handler | Persistent discovery subprocess with sentinel-based iteration; dict-based handler tracking |
| **WslDockerTcpHandler** | Handler | Reads a single container's `/proc/net/tcp` via `docker exec` + `SubprocessDrain` |
| **SubprocessDrain** | Shared | Persistent subprocess lifecycle, bounded queue, daemon reader thread, sentinel-based drain (queue.get with timeout, last-pair scanning) |
| **Wakelock Engine** | OS Interface | Acquires/releases Windows wake lock via `kernel32.SetThreadExecutionState` |
| **dump_iphlpapi.py** | Utility | Dumps raw TCP table buffer for binary analysis |
| **wsl_tcp_monitor.sh** | Utility | WSL helper script for `/proc/net/tcp` monitoring |

## Data Flow

```mermaid
flowchart LR
    ConfigFile["config.toml"] --> Defaults["DEFAULTS"]
    Defaults --> ConfigLoader["load_config()"]
    ConfigLoader --> Monitor["TcpConnectionMonitor"]

    Monitor --> WinHandler["WindowsTcpHandler"]
    Monitor -. wsl_monitoring .-> WslHandler["WslTcpHandler"]
    Monitor -. docker .-> DockerMgr["WslDockerManager"]

    WinHandler --> Iphlpapi["iphlpapi.dll"]
    WslHandler --> WSL["WSL /proc/net/tcp"]
    DockerMgr --> Docker["Docker in WSL"]

    WslHandler --> Drain["SubprocessDrain<br/>loop + queue + sentinel"]
    DockerMgr --> Drain

    WinHandler --> ActiveCheck{"has_active_connections()"}
    WslHandler --> ActiveCheck
    DockerMgr --> ActiveCheck

    ActiveCheck --> |"active"| Acquire["_acquire()\nSetThreadExecutionState"]
    ActiveCheck --> |"no active"| Release["_release()\nSetThreadExecutionState"]

    Acquire --> Kernel32["kernel32.dll"]
    Release --> Kernel32
```

## Handler Interface

All handlers implement `TcpConnectionSource` and return a uniform connection dict:

```python
{
    "state": int,           # TCP state (5 = ESTABLISHED)
    "local_addr": str,      # Local IPv4 address
    "local_port": int,      # Local port number
    "remote_addr": str,     # Remote IPv4 address
    "remote_port": int,     # Remote port number
    "source": ConnectionSource,  # WINDOWS | WSL | WSL_DOCKER
    "container_id": str,    # Docker container short ID (WSL_DOCKER only)
}
```
