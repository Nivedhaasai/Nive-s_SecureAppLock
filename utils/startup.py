"""
Nive'secureAppLock - Windows startup integration.
Adds/removes a registry Run key under HKCU so Nive'secureAppLock launches at login.
"""

import os
import sys
import winreg
from utils.logger import setup_logger

logger = setup_logger()

_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "NiveSecureAppLock"
_OLD_APP_NAME = "SecureAppLock"  # legacy registry key to clean up


def _migrate_old_registry_key() -> None:
    """Remove the old 'SecureAppLock' registry entry if it exists."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, _OLD_APP_NAME)
            logger.info("Removed legacy startup entry '%s'.", _OLD_APP_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except OSError:
        pass


def _get_exe_command() -> str:
    """Return the command to run this application.

    Uses pythonw.exe (window-less Python) so no console window
    flashes at login — fully silent background startup.
    """
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    # Prefer pythonw.exe for invisible startup; fall back to python.exe
    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    exe = pythonw if os.path.isfile(pythonw) else sys.executable
    return f'"{exe}" "{script}"'


def enable_startup() -> bool:
    """Add Nive'secureAppLock to Windows startup."""
    _migrate_old_registry_key()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _get_exe_command())
        winreg.CloseKey(key)
        logger.info("Startup entry added to registry.")
        return True
    except OSError as e:
        logger.error("Failed to add startup entry: %s", e)
        return False


def disable_startup() -> bool:
    """Remove Nive'secureAppLock from Windows startup."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _APP_NAME)
        winreg.CloseKey(key)
        logger.info("Startup entry removed from registry.")
        return True
    except FileNotFoundError:
        logger.info("Startup entry was not present.")
        return True
    except OSError as e:
        logger.error("Failed to remove startup entry: %s", e)
        return False


def is_startup_enabled() -> bool:
    """Check if the startup entry exists."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _APP_NAME)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False
