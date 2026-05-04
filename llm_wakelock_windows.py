import subprocess
import time
import ctypes

PORTS = [8001, 11434]

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

def acquire():
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    )

def release():
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

def has_active_connections():
    out = subprocess.check_output(
        ["netstat", "-ano"], text=True
    )
    for line in out.splitlines():
        for PORT in PORTS:
            if f":{PORT}" in line and "ESTABLISHED" in line:
                return True
    return False

wakelock = False

print(f'Checking for established connections on ports {PORTS}')

while True:
    active = has_active_connections()

    if active and not wakelock:
        acquire()
        wakelock = True

    elif not active and wakelock:
        release()
        wakelock = False

    time.sleep(60)