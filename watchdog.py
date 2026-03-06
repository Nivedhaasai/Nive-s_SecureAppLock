"""
Nive'secureAppLock - Watchdog process.
Runs as a detached child that monitors the main Nive'secureAppLock process.
If the main process disappears without writing a graceful-exit sentinel
file, the watchdog automatically relaunches it.

Usage (internal - spawned by main.py):
    python watchdog.py <main_pid> <main_script_path> <sentinel_path>
"""

import os
import sys
import time
import subprocess

import psutil


def _is_main_running(pid: int) -> bool:
    """Check if the main process is still alive."""
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _graceful_exit_requested(sentinel_path: str) -> bool:
    """Check if the main process wrote a sentinel file signaling graceful exit."""
    return os.path.exists(sentinel_path)


def _cleanup_sentinel(sentinel_path: str) -> None:
    """Remove the sentinel file after reading it."""
    try:
        os.remove(sentinel_path)
    except OSError:
        pass


def _relaunch_main(main_script: str) -> None:
    """Relaunch the main Nive'secureAppLock process as a detached process."""
    subprocess.Popen(
        [sys.executable, main_script],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_watchdog(main_pid: int, main_script: str, sentinel_path: str) -> None:
    """
    Main watchdog loop.
    Monitors the given PID and relaunches from main_script if it dies
    without a graceful-exit sentinel file being present.
    """
    poll_interval = 2  # seconds

    while True:
        time.sleep(poll_interval)

        if _is_main_running(main_pid):
            # Main process is alive - check if graceful exit was requested
            # (main writes sentinel then terminates)
            if _graceful_exit_requested(sentinel_path):
                _cleanup_sentinel(sentinel_path)
                break  # Authorized exit - watchdog should also stop
            continue

        # Main process is gone - check why
        if _graceful_exit_requested(sentinel_path):
            # Authorized exit - clean up and stop watchdog
            _cleanup_sentinel(sentinel_path)
            break

        # Unauthorized termination - relaunch
        _relaunch_main(main_script)

        # Wait for the new process to start, then exit this watchdog instance.
        # The new main process will spawn its own watchdog.
        time.sleep(3)
        break


def main():
    if len(sys.argv) != 4:
        sys.exit(1)

    try:
        main_pid = int(sys.argv[1])
        main_script = sys.argv[2]
        sentinel_path = sys.argv[3]
    except (ValueError, IndexError):
        sys.exit(1)

    run_watchdog(main_pid, main_script, sentinel_path)


if __name__ == "__main__":
    main()
