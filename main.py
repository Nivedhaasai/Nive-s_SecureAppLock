"""
Nive'secureAppLock - Main entry point.

Responsibilities:
  - Single-instance lock via a Windows named mutex.
  - Tamper-proof watchdog: spawns a detached watchdog process that
    relaunches Nive'secureAppLock if it is terminated without authentication.
  - Graceful exit via sentinel file: only an authenticated Exit writes
    the sentinel so the watchdog knows not to restart.
  - Orchestrates the WMI process watcher, lock-screen UI, system tray,
    settings window, and first-run setup dialog.

Usage:
    python main.py     # normal launch
"""

import ctypes
import os
import subprocess
import sys
import tempfile

# -- Fix import path so submodules resolve ---------------------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from config.config import AppConfig, LockedApp
from monitor.process_watcher import ProcessWatcher
from ui.lock_screen import LockScreen
from ui.tray_icon import TrayIcon, AuthGateDialog
from ui.setup_dialog import SetupDialog
from ui.settings_window import SettingsWindow, ChangePinDialog
from ui.app_picker_dialog import AppPickerDialog
from utils.startup import enable_startup, is_startup_enabled
from utils.logger import setup_logger

logger = setup_logger()

# -- Named mutex for single-instance lock ---------------------------------
_MUTEX_NAME = "Global\\NiveSecureAppLock_SingleInstance"

# -- Sentinel file for graceful exit coordination with watchdog ------------
_SENTINEL_DIR = os.path.join(tempfile.gettempdir(), "NiveSecureAppLock")
_SENTINEL_PATH = os.path.join(_SENTINEL_DIR, "graceful_exit.sentinel")

# -- Paths -----------------------------------------------------------------
_MAIN_SCRIPT = os.path.abspath(__file__)
_WATCHDOG_SCRIPT = os.path.join(_ROOT, "watchdog.py")


def _acquire_mutex():
    """Try to create the named mutex. Returns the handle or exits."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    last_err = kernel32.GetLastError()
    if last_err == 183:  # ERROR_ALREADY_EXISTS
        logger.warning("Another instance is already running. Exiting.")
        sys.exit(0)
    return handle


# -- Watchdog management ---------------------------------------------------

def _cleanup_stale_sentinel() -> None:
    """Remove any stale sentinel from a previous run."""
    try:
        os.remove(_SENTINEL_PATH)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug("Could not clean sentinel: %s", e)


def _spawn_watchdog() -> None:
    """Spawn the watchdog as a fully detached child process."""
    os.makedirs(_SENTINEL_DIR, exist_ok=True)
    _cleanup_stale_sentinel()

    pid = os.getpid()
    subprocess.Popen(
        [sys.executable, _WATCHDOG_SCRIPT, str(pid), _MAIN_SCRIPT, _SENTINEL_PATH],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("Watchdog spawned for PID %d (sentinel: %s)", pid, _SENTINEL_PATH)


def _write_graceful_exit_sentinel() -> None:
    """
    Write the sentinel file so the watchdog knows this is an
    authorized exit and should NOT relaunch.
    """
    try:
        os.makedirs(_SENTINEL_DIR, exist_ok=True)
        with open(_SENTINEL_PATH, "w") as f:
            f.write("graceful")
        logger.info("Graceful exit sentinel written.")
    except OSError as e:
        logger.error("Failed to write sentinel: %s", e)


# -- App launch helper -----------------------------------------------------

def _launch_app(app: LockedApp) -> None:
    """Launch an application after successful authentication."""
    cmd = app.launch_command
    try:
        if app.is_store_app:
            os.startfile(cmd)
            logger.info("Launched Store app: %s", cmd)
        else:
            if os.path.isfile(cmd):
                subprocess.Popen([cmd])
                logger.info("Launched desktop app: %s", cmd)
            else:
                subprocess.Popen(cmd, shell=True)
                logger.info("Launched app via shell: %s", cmd)
    except Exception as e:
        logger.error("Failed to launch app %s: %s", cmd, e)


# -- Main application -----------------------------------------------------

class NiveSecureAppLockApp:
    """Top-level application controller."""

    def __init__(self):
        self._app = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._app.setApplicationName("Nive'secureAppLock")

        # Load config
        self._config = AppConfig.load()

        # First-run setup: require a PIN
        if not self._config.pin_hash:
            dlg = SetupDialog()
            if dlg.exec() != SetupDialog.DialogCode.Accepted or not dlg.result_hash:
                logger.info("Setup cancelled - exiting.")
                # Write sentinel so watchdog doesn't relaunch during first-run cancel
                _write_graceful_exit_sentinel()
                sys.exit(0)
            self._config.pin_hash = dlg.result_hash
            self._config.save()

        # Ensure auto-start is registered — enterprise: always-on by default
        if self._config.auto_start and not is_startup_enabled():
            enable_startup()
            logger.info("Auto-start enabled (Nive'secureAppLock default).")

        # Process watcher (WMI event-driven)
        self._watcher = ProcessWatcher(self._config)

        # Lock screen
        self._lock = LockScreen(
            config=self._config,
            fingerprint_enabled=self._config.fingerprint_enabled,
        )
        self._lock.authenticated.connect(self._on_authenticated)

        # Settings window (lazy - created on demand)
        self._settings: SettingsWindow | None = None

        # System tray - pass config and pin_hash for authenticated exit
        self._tray = TrayIcon(self._config)
        self._tray.settings_requested.connect(self._show_settings)
        self._tray.add_app_requested.connect(self._add_app_from_tray)
        self._tray.pin_change_requested.connect(self._change_pin_from_tray)
        self._tray.quit_authenticated.connect(self._on_authenticated_quit)

        # Wire watcher -> lock screen
        self._watcher.app_blocked.connect(self._on_app_blocked)

        # Timer to re-lock apps whose processes have exited
        self._relock_timer = QTimer()
        self._relock_timer.setInterval(2000)
        self._relock_timer.timeout.connect(self._watcher.check_unlocked_still_running)
        self._relock_timer.start()

    def run(self) -> int:
        self._watcher.start()
        logger.info(
            "Nive'secureAppLock running. Protecting: %s",
            ", ".join(a.name for a in self._config.locked_apps),
        )
        return self._app.exec()

    # -- slots -------------------------------------------------------------

    def _on_app_blocked(self, app_name: str) -> None:
        """Watcher detected a locked app launch - show the lock screen."""
        if self._watcher.is_unlocked(app_name):
            return  # stale signal fired after the user already authenticated
        self._lock.show_for_app(app_name)

    def _on_authenticated(self, app_name: str) -> None:
        """User authenticated - unlock and relaunch the app."""
        self._watcher.unlock(app_name)
        locked_app = self._config.find_app_by_name(app_name)
        if locked_app:
            _launch_app(locked_app)

    def _show_settings(self) -> None:
        """Open the settings window - requires authentication first."""
        auth = AuthGateDialog(
            title="Authenticate to Open Settings",
            subtitle="Verify your identity to access Nive'secureAppLock settings.",
            pin_hash=self._config.pin_hash,
            fingerprint_enabled=self._config.fingerprint_enabled,
        )
        if auth.exec() != auth.DialogCode.Accepted:
            return

        if self._settings is None or not self._settings.isVisible():
            self._settings = SettingsWindow(self._config)
            self._settings.apps_changed.connect(self._on_apps_changed)
            self._settings.pin_changed.connect(self._on_pin_changed)
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _add_app_from_tray(self) -> None:
        """Quick add app from tray — requires authentication first."""
        auth = AuthGateDialog(
            title="Authenticate to Add App",
            subtitle="Verify your identity to modify locked apps.",
            pin_hash=self._config.pin_hash,
            fingerprint_enabled=self._config.fingerprint_enabled,
        )
        if auth.exec() != auth.DialogCode.Accepted:
            return

        already_locked = self._config.get_all_process_names()
        picker = AppPickerDialog(already_locked)
        if picker.exec() != picker.DialogCode.Accepted or not picker.selected_app:
            return

        app = picker.selected_app
        new_app = LockedApp(
            name=app.name,
            process_names=app.process_names,
            launch_command=app.launch_command,
            is_store_app=app.is_store_app,
        )
        self._config.add_app(new_app)
        self._tray.showMessage(
            "Nive'secureAppLock",
            f"{app.name} has been added to locked apps.",
            self._tray.MessageIcon.Information,
        )
        logger.info("Added app from tray: %s (%s)", app.name, app.process_names)

    def _change_pin_from_tray(self) -> None:
        """Change PIN from tray menu — requires authentication first."""
        auth = AuthGateDialog(
            title="Authenticate to Change PIN",
            subtitle="Verify your identity to change your PIN.",
            pin_hash=self._config.pin_hash,
            fingerprint_enabled=self._config.fingerprint_enabled,
        )
        if auth.exec() != auth.DialogCode.Accepted:
            return

        dlg = ChangePinDialog()
        if dlg.exec() == ChangePinDialog.DialogCode.Accepted and dlg.new_hash:
            self._config.pin_hash = dlg.new_hash
            self._config.save()
            self._lock.update_pin_hash(dlg.new_hash)
            self._tray.showMessage(
                "Nive'secureAppLock",
                "PIN updated successfully.",
                self._tray.MessageIcon.Information,
            )

    def _on_apps_changed(self) -> None:
        logger.info(
            "Locked apps updated: %s",
            ", ".join(a.name for a in self._config.locked_apps),
        )

    def _on_pin_changed(self, new_hash: str) -> None:
        self._lock.update_pin_hash(new_hash)

    def _on_authenticated_quit(self) -> None:
        """
        User authenticated via fingerprint or PIN to exit.
        Write the graceful-exit sentinel so the watchdog stops,
        then shut down cleanly.
        """
        logger.info("Authenticated exit requested.")
        _write_graceful_exit_sentinel()
        self._watcher.stop()
        self._relock_timer.stop()
        self._tray.hide()
        self._app.quit()


# -- Entry point -----------------------------------------------------------

def main():
    # Single instance
    _mutex = _acquire_mutex()  # noqa: F841 - must keep handle alive

    # Spawn watchdog
    _spawn_watchdog()

    logger.info("=" * 60)
    logger.info("Nive'secureAppLock starting ...")
    logger.info("=" * 60)

    app = NiveSecureAppLockApp()
    code = app.run()
    sys.exit(code)


if __name__ == "__main__":
    main()
