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
        WSH->>DD: SubprocessDrain(command, config)
    end

    alt wsl_docker_monitoring_max >= 1
        TC->>WD: WslDockerManager(config)
        WD->>DD: SubprocessDrain("docker ps", config)
        DD->>OS: wsl.exe -e sh -c "while true; do docker ps; sleep N; done"
    end

    loop polling_interval
        TC->>TC: has_active_connections()
        TC->>WH: get_connections()
        WH->>OS: GetExtendedTcpTable()
        OS-->>WH: TCP table

        alt WSL enabled
            TC->>WSH: get_connections()
            WSH->>DD: drain()
            DD->>DD: queue.get(timeout=remaining) — block-wait
            DD->>DD: scan last 2 SENTINEL, return lines
            DD-->>WSH: tcp lines
            WSH->>WSH: parse each line
            WSH-->>TC: connections
        end

        alt Docker enabled
            TC->>WD: get_connections()
            loop each handler in _handlers dict
                WD->>WDC: get_connections()
                WDC->>DD: drain()
                DD->>DD: queue.get(timeout=remaining)
                DD->>DD: scan last 2 sentinels, return lines
                DD-->>WDC: tcp lines
                WDC->>WDC: parse, filter ESTABLISHED
                WDC-->>WD: container connections
            end
            WD-->>TC: aggregated connections
        end

        alt active connections found
            TC->>TC: _acquire() — SetThreadExecutionState
        else no active connections
            alt wakelock held
                TC->>TC: _release()
            end
        end

        TC->>TC: sleep(polling_interval)
    end
```

## WSL Subprocess Lifecycle (Death Detection + Auto-Restart)

```mermaid
sequenceDiagram
    participant H as Handler
    participant DD as SubprocessDrain
    participant Q as Queue
    participant T as drain thread
    participant P as subprocess
    participant OS as WSL

    H->>DD: SubprocessDrain(command, config)
    H->>DD: start()
    DD->>P: wsl.exe -e sh -c "<_full_command>"
    P->>OS: runs inside WSL
    P->>Q: stdout PIPE
    H->>T: Thread(_drain_loop, P, Q)
    T->>Q: lines continuously

    loop polling cycle
        H->>DD: drain()

        alt process alive + sentinel pair
            DD->>DD: queue.get() → scan sentinels
            DD-->>H: tcp lines
        else process dead (poll != None)
            Note over DD,OS: subprocess died (sleep, shutdown, restart)
            DD->>DD: log "[WARN] {owner} subprocess died"
            DD->>DD: _restart_if_needed()
            alt cooldown expired + failures >= threshold
                DD->>DD: stop()
                DD->>DD: sleep(0.5s)
                DD->>P: start() → wsl.exe again
                DD->>DD: log "[INFO] {owner} restarted"
            end
            DD-->>H: cached output or []
        else sentinel miss
            Note over DD: loop broke but process still alive
            DD->>DD: increment consecutive_failures
            DD->>DD: _restart_if_needed()
            DD-->>H: cached output or []

        alt success after restart
            DD->>DD: log "[INFO] {owner} re-established"
        end
    end

    H->>DD: stop()
    DD->>P: terminate/kill
    DD->>T: join
```

## Docker Container Discovery (Persistent Subprocess)

```mermaid
sequenceDiagram
    participant DD as SubprocessDrain
    participant WD as WslDockerManager
    participant OS as WSL / Docker

    Note over DD: Persistent loop: while true; do docker ps; sleep N; done
    DD->>OS: wsl.exe -e sh -c "while true; do docker ps; sleep N; done"
    OS-->>DD: stdout container lines

    loop each polling cycle
        WD->>DD: drain()
        DD->>DD: queue.get(timeout=remaining)
        DD->>DD: scan last 2 SENTINEL, return lines
        DD-->>WD: container ID lines
        WD->>WD: diff current_ids vs _handlers keys
        alt new container
            WD->>WDC: WslDockerTcpHandler(config, container_id)
            WDC->>DD: start()
            DD->>OS: docker exec <id> sh -c "while true; do cat /proc/net/tcp; sleep N; done"
            OS-->>DD: /proc/net/tcp output
        else stopped container
            WD->>WDC: handler.cleanup()
            WDC->>DD: stop()
        end
    end
```
