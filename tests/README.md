# Tests

Most tests run on Linux by mocking external dependencies without WSL

## Running tests

```bash
python -m pytest tests/ -v                     # all non-Windows tests
python -m pytest tests/ -m ""                  # all tests, including Windows live tests
python -m pytest tests/ --cov=.                # with coverage (requires pytest-cov)
python -m pytest tests/ --cov=. --cov-report=html  # HTML report in htmlcov/
```

## Test markers

| Marker | Description |
|---|---|
| `@pytest.mark.windows` | Requires Windows OS — skipped on Linux (use `-m ""` to run them) |

## Mocking strategy

- **Windows APIs**: `ctypes.windll.iphlpapi` and `kernel32` mocked via `MagicMock`
- **Subprocesses**: `subprocess.Popen` and `CREATE_NO_WINDOW` patched — no real WSL/Docker calls
- **Time**: `time.time()` and `datetime.datetime.now()` patched for timing tests
- **Handlers**: Dependency injection pattern — mock handlers passed to `TcpConnectionMonitor` via the `handlers=` parameter
