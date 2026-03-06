"""
Nive'secureAppLock - Windows Hello fingerprint/face authentication.

Calls the companion C# service (SecureHelloAuth.exe) which uses the
native Windows.Security.Credentials.UI.UserConsentVerifier API to
trigger Windows Hello biometric authentication (fingerprint / face).

Enterprise-grade features:
  - Grants foreground window rights to the C# process via
    AllowSetForegroundWindow so the Windows Security dialog appears
    immediately on top — same responsiveness as laptop unlock.
  - Uses STARTUPINFO(SW_HIDE) instead of CREATE_NO_WINDOW so the
    process retains a window station for proper dialog focus.
  - Custom dialog message showing which app is being unlocked.
"""

import ctypes
import os
import subprocess
import sys
from utils.logger import setup_logger

logger = setup_logger()

# -- Win32 helpers for foreground management --------------------------------

def _allow_set_foreground_window(pid: int) -> None:
    """Grant a process the right to set itself as the foreground window."""
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(pid)
    except Exception:
        pass


# -- Locate the compiled C# authentication service ------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SEARCH_PATHS = [
    # Published output (dotnet publish)
    os.path.join(_ROOT, "service", "SecureHelloAuth", "bin", "Release",
                 "net8.0-windows10.0.17763.0", "win-x64", "publish", "SecureHelloAuth.exe"),
    # Build output (dotnet build)
    os.path.join(_ROOT, "service", "SecureHelloAuth", "bin", "Release",
                 "net8.0-windows10.0.17763.0", "win-x64", "SecureHelloAuth.exe"),
    # Debug build
    os.path.join(_ROOT, "service", "SecureHelloAuth", "bin", "Debug",
                 "net8.0-windows10.0.17763.0", "win-x64", "SecureHelloAuth.exe"),
    # Placed alongside main.py for convenience
    os.path.join(_ROOT, "SecureHelloAuth.exe"),
    # Inside service/ folder directly
    os.path.join(_ROOT, "service", "SecureHelloAuth.exe"),
]


def _find_auth_exe() -> str | None:
    """Locate SecureHelloAuth.exe by searching known paths."""
    for path in _SEARCH_PATHS:
        if os.path.isfile(path):
            return path
    return None


def _run_auth_command(command: str, timeout: int = 30,
                      extra_args: list[str] | None = None) -> tuple[int, str]:
    """
    Run SecureHelloAuth.exe with the given command.

    Uses Popen (not run) so we can grab the PID and grant foreground
    rights before the C# process shows the Windows Security dialog.

    Uses STARTUPINFO(SW_HIDE) instead of CREATE_NO_WINDOW so the
    process retains a window station — needed for the Windows Security
    dialog to get foreground focus and the fingerprint sensor to
    activate immediately.
    """
    exe = _find_auth_exe()
    if exe is None:
        logger.error(
            "SecureHelloAuth.exe not found. Build it with:\n"
            "  cd service/SecureHelloAuth\n"
            "  dotnet publish -c Release -r win-x64 --self-contained false"
        )
        return (-1, "FAILED:ExeNotFound")

    try:
        cmd = [exe, command]
        if extra_args:
            cmd.extend(extra_args)

        # STARTUPINFO with SW_HIDE: hides the console window but the
        # process still has a window station, allowing the Windows
        # Security dialog to appear in the foreground.
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=si,
        )

        # Grant foreground window rights to the C# process IMMEDIATELY
        # so the Windows Security dialog pops to top without delay.
        _allow_set_foreground_window(proc.pid)

        stdout, stderr = proc.communicate(timeout=timeout)
        stdout = stdout.strip()

        logger.debug("SecureHelloAuth %s → exit %d, stdout: %s",
                      command, proc.returncode, stdout)
        if stderr.strip():
            logger.debug("SecureHelloAuth stderr: %s", stderr.strip())

        return (proc.returncode, stdout)

    except subprocess.TimeoutExpired:
        logger.warning("SecureHelloAuth timed out after %d seconds.", timeout)
        proc.kill()
        proc.wait()
        return (1, "FAILED:Timeout")
    except FileNotFoundError:
        logger.error("SecureHelloAuth.exe not found at: %s", exe)
        return (-1, "FAILED:ExeNotFound")
    except OSError as e:
        logger.error("Failed to run SecureHelloAuth: %s", e)
        return (1, "FAILED:OSError")


def is_windows_hello_available() -> bool:
    """Check if Windows Hello biometric authentication is available."""
    exit_code, stdout = _run_auth_command("check", timeout=10)
    available = exit_code == 0 and stdout == "AVAILABLE"
    logger.info("Windows Hello available: %s (%s)", available, stdout)
    return available


def authenticate_windows_hello(app_name: str = "") -> bool:
    """
    Trigger Windows Hello biometric authentication.

    Returns True if the user successfully verifies via fingerprint,
    face recognition, or Windows Hello PIN.

    The app_name is shown in the Windows Security dialog so the user
    knows exactly which app they are unlocking.
    """
    message = f"Unlock {app_name}" if app_name else "Scan your fingerprint to unlock"
    logger.info("Triggering Windows Hello for: %s", app_name or "(generic)")

    exit_code, stdout = _run_auth_command(
        "verify", timeout=60, extra_args=[message],
    )

    if exit_code == 0 and stdout == "SUCCESS":
        logger.info("Windows Hello authentication successful.")
        return True
    elif "CANCELED" in stdout:
        logger.info("User cancelled Windows Hello dialog.")
        return False
    else:
        logger.warning("Windows Hello authentication failed: %s (exit %d)", stdout, exit_code)
        return False
