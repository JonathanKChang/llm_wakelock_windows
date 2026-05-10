# Execution Flow

## Application Startup & Main Loop

```mermaid
sequenceDiagram
    participant M as main()
    participant LC as load_config()
    participant TC as TcpConnectionMonitor
    participant WH as WindowsTcpHandler
    participant WSH as WslTcpHandler
    participant WD as WslDockerManager
    participant DD as SubprocessDrain (discovery)
    participant WDC as WslDockerTcpHandler x N
    participant OS as OS / WSL / Docker

    M->>LC: load config from config.toml (optional)
    LC-->>M: config dict (defaults + overrides)
    M->>TC: TcpConnectionMonitor(config)

    alt wsl_monitoring enabled
        TC->>WSH: WslTcpHandler(config)
        WSH->>DD: SubprocessDrain(command, sentinel="/proc/net/tcp")
        Note over WSH,DD: persistent loop: echo /proc/net/tcp#59; cat /proc/net/tcp#59; sleep N
    end

    alt wsl_docker_monitoring_max >= 1
        TC->>WD: WslDockerManager(config)
        WD->>DD: SubprocessDrain("docker ps --format...", sentinel="DISCOVERY")
        Note over WD,DD: persistent loop: echo DISCOVERY#59; docker ps#59; sleep interval
        WD->>DD: start()
        DD->>OS: wsl.exe -e sh -c "while true#59; do echo DISCOVERY#59; docker ps#59; sleep N#59; done"
        loop discovery interval
            DD->>DD: drain() — returns lines after last DISCOVERY sentinel
            DD-->>WD: container ID + name lines
            WD->>WD: diff handlers, add new / remove stopped
        end
    end

    loop polling_interval
        TC->>TC: has_active_connections()
        TC->>WH: get_connections()
        WH->>OS: GetExtendedTcpTable()
        OS-->>WH: TCP table

        alt WSL enabled
            TC->>WSH: get_connections()
            WSH->>DD: drain() — returns lines after /proc/net/tcp header
            DD-->>WSH: tcp lines
            WSH->>WSH: parse each line
            WSH-->>TC: connections
        end

        alt Docker enabled
            TC->>WD: get_connections()
            loop each handler in _handlers dict
                WD->>WDC: get_connections()
                WDC->>DD: drain() — returns lines after header
                DD-->>WDC: tcp lines
                WDC->>WDC: parse, filter ESTABLISHED
                WDC-->>WD: container connections
            end
            WD-->>TC: aggregated connections
        end

        alt active connections found
            TC->>TC: _acquire() — SetThreadExecutionState
            TC->>M: print wakelock acquired + connections
        else no active connections
            alt wakelock held
                TC->>TC: _release() — release wakelock
                TC->>M: print wakelock released
            end
        end

        TC->>TC: sleep(polling_interval)
    end
```

## Docker Container Discovery (Persistent Subprocess)

```mermaid
sequenceDiagram
    participant DD as SubprocessDrain
    participant WD as WslDockerManager
    participant OS as WSL / Docker

    Note over DD: Persistent loop: echo DISCOVERY#59; docker ps#59; sleep N
    DD->>OS: wsl.exe -e sh -c "while true#59; do echo DISCOVERY#59; docker ps --format '{{.ID}}\\t{{.Names}}'#59; sleep N#59; done"
    OS-->>DD: stdout: DISCOVERY /n container lines /n DISCOVERY /n container lines /n ...

    loop each polling cycle
        WD->>DD: drain()
        DD->>DD: find last DISCOVERY sentinel
        DD->>DD: return lines after sentinel only
        DD-->>WD: ["abc123\tcontainer1", "def456\tcontainer2", ...]
        WD->>WD: diff current_ids vs _handlers keys
        alt new container
            WD->>WDC: WslDockerTcpHandler(config, container_id)
            WDC->>DD: SubprocessDrain.start()
            DD->>OS: docker exec <id> sh -c "while true#59; do cat /proc/net/tcp#59; sleep N#59; done"
            OS-->>DD: /proc/net/tcp output
        else stopped container
            WD->>WDC: handler.cleanup()
            WDC->>DD: stop()
        end
    end
```

## WSL Subprocess Lifecycle (via SubprocessDrain)

```mermaid
sequenceDiagram
    participant H as Handler
    participant DD as SubprocessDrain
    participant Q as Queue (bounded)
    participant T as drain thread
    participant P as subprocess
    participant OS as WSL

    H->>DD: SubprocessDrain(command, sentinel="/proc/net/tcp")
    Note over DD: _full_command = "while true#59; do echo /proc/net/tcp#59; cat /proc/net/tcp#59; sleep N#59; done"

    H->>DD: start()
    DD->>P: wsl.exe -e sh -c "<_full_command>"
    P->>OS: runs inside WSL
    OS-->>P: /proc/net/tcp output (repeated)
    P->>Q: stdout PIPE
    H->>T: Thread(target=_drain_loop, args=(P, Q), daemon=True)
    T->>Q: puts lines into queue continuously

    loop polling_interval
        H->>DD: drain()
        DD->>Q: get_nowait all available lines
        DD->>DD: find last "/proc/net/tcp" sentinel
        DD-->>H: lines after sentinel only
        H->>H: parse each line (skip header line)
    end

    H->>DD: stop()
    DD->>P: terminate() / kill()
    DD->>T: join(timeout=3)
```
