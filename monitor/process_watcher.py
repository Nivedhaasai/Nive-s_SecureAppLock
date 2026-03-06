"""
Nive'secureAppLock - WMI-based process watcher.
Uses WMI events for process detection instead of polling.

Strategy:
  1. Try Win32_ProcessStartTrace (instant, requires admin).
  2. Fall back to __InstanceCreationEvent on Win32_Process
     (0.5-second WMI-managed polling, works without admin).
"""

import threading
import subprocess
import time

import psutil
try:
    import win32gui
    import win32process
    _WIN32GUI = True
except ImportError:
    _WIN32GUI = False
from PyQt6.QtCore import QObject, pyqtSignal

from config.config import AppConfig
from utils.logger import setup_logger

logger = setup_logger()


class ProcessWatcher(QObject):
    """
    Monitors process creation using WMI events.
    Kills locked apps immediately and emits app_blocked signal.

    Signals
    -------
    app_blocked(str)
        Emitted with the app name when a locked app is detected and killed.
    """

    app_blocked = pyqtSignal(str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self._config = config
        self._unlocked: set[str] = set()
        self._unlock_times: dict[str, float] = {}
        self._silent_relock_times: dict[str, float] = {}  # suppresses lock screen after silent re-lock
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._poller_thread: threading.Thread | None = None

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        """Start the WMI event watcher and fast psutil poller in background daemon threads."""
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._wmi_watch_loop, daemon=True, name="ProcessWatcher"
        )
        self._thread.start()
        self._poller_thread = threading.Thread(
            target=self._psutil_poll_loop, daemon=True, name="ProcessWatcherPoller"
        )
        self._poller_thread.start()
        logger.info("Process watcher started (WMI event-driven + psutil poller).")

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=3)
        if self._poller_thread:
            self._poller_thread.join(timeout=3)
        logger.info("Process watcher stopped.")

    def unlock(self, app_name: str) -> None:
        """Mark an app as unlocked so the watcher allows it."""
        self._unlocked.add(app_name)
        self._unlock_times[app_name] = time.monotonic()
        logger.info("Unlocked: %s", app_name)

    def lock(self, app_name: str) -> None:
        """Re-lock an app."""
        self._unlocked.discard(app_name)
        logger.info("Locked: %s", app_name)

    def lock_all(self) -> None:
        self._unlocked.clear()

    def is_unlocked(self, app_name: str) -> bool:
        return app_name in self._unlocked

    def check_unlocked_still_running(self) -> None:
        """
        Called periodically from a QTimer.
        Re-locks an app when:
          - It has no visible window (user closed it, even if background process lingers), OR
          - 30 minutes have passed since unlock (safety timeout).

        When re-locking, any lingering background processes are killed silently
        so the lock screen does NOT pop up until the user actively opens the app again.
        """
        now = time.monotonic()
        _GRACE   = 10.0           # seconds after unlock before checks start
        _TIMEOUT = 30 * 60        # 30 minutes: force re-lock regardless
        for app in list(self._config.locked_apps):
            if app.name not in self._unlocked:
                continue
            elapsed = now - self._unlock_times.get(app.name, 0)
            if elapsed < _GRACE:
                continue
            # Safety timeout: re-lock after 30 minutes no matter what
            if elapsed > _TIMEOUT:
                self._silent_relock_times[app.name] = now
                self.lock(app.name)
                self._kill_silent(app)
                logger.info("App %s auto-relocked after 30-minute timeout.", app.name)
                continue
            # Re-lock if the app has no visible window
            if not self._has_visible_window(app):
                self._silent_relock_times[app.name] = now
                self.lock(app.name)
                self._kill_silent(app)
                logger.info("App %s closed - re-locked silently.", app.name)

    # -- internals ---------------------------------------------------------

    def _find_matching_app(self, process_name: str, pid: int):
        """
        Find the LockedApp that matches a process name + PID.
        For PWA apps (pwa_app_id set), also checks the command line
        for --app-id= to avoid false matches on shared executables
        like msedge.exe.
        """
        proc_lower = process_name.lower()
        for app in self._config.locked_apps:
            for pn in app.process_names:
                if pn.lower() == proc_lower:
                    if app.pwa_app_id:
                        if self._is_pwa_match(pid, app.pwa_app_id):
                            return app
                    else:
                        return app
        return None

    @staticmethod
    def _is_pwa_match(pid: int, pwa_app_id: str) -> bool:
        """Check if a process was launched with the given PWA --app-id."""
        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()
            target = f"--app-id={pwa_app_id}"
            return any(target in arg for arg in cmdline)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _kill_silent(self, app) -> None:
        """
        Kill all remaining processes of a re-locked app WITHOUT emitting
        app_blocked. Used when the user closed the app — we clean up the
        background process quietly so the lock screen does not pop up
        uninvited. The lock screen will appear the next time the user
        actively opens the app.
        """
        names_lower = {n.lower() for n in app.process_names}
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                pname = (proc.info["name"] or "").lower()
                if pname in names_lower:
                    # For PWA apps, only kill the process with the matching app-id
                    if app.pwa_app_id:
                        if not self._is_pwa_match(proc.info["pid"], app.pwa_app_id):
                            continue
                    self._kill_process(app.name, pname, proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _has_visible_window(self, app) -> bool:
        """
        Return True if the app has a visible, titled top-level window.

        For PWA apps, checks that a matching msedge.exe process (with the
        correct --app-id) is still running — since PWA windows close when
        the user closes the app.  For normal apps, uses EnumWindows to
        check for a visible window belonging to one of the process names.
        Falls back to True (assume visible) if win32gui is unavailable.
        """
        process_names = app.process_names

        # PWA shortcut: check if the PWA process is still alive
        if app.pwa_app_id:
            names_lower = {n.lower() for n in process_names}
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    pname = (proc.info["name"] or "").lower()
                    if pname in names_lower and self._is_pwa_match(proc.info["pid"], app.pwa_app_id):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False

        if not _WIN32GUI:
            # Fallback: just check process existence
            names_lower = {n.lower() for n in process_names}
            for proc in psutil.process_iter(["name"]):
                try:
                    if (proc.info["name"] or "").lower() in names_lower:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False

        # Build the set of PIDs belonging to protected processes
        names_lower = {n.lower() for n in process_names}
        protected_pids: set[int] = set()
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                if (proc.info["name"] or "").lower() in names_lower:
                    protected_pids.add(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not protected_pids:
            return False

        result = [False]

        def _enum_cb(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if not win32gui.GetWindowText(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in protected_pids:
                    result[0] = True
                    return False  # stop enumeration
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            return True  # safe default: assume visible if we can't check

        return result[0]

    def _wmi_watch_loop(self) -> None:
        """Main WMI event loop - runs in a background thread."""
        # Kill any protected apps that were already running before we started
        self._startup_scan()

        try:
            import wmi
        except ImportError:
            logger.error("WMI module not available. Install with: pip install wmi pywin32")
            return

        # Strategy 1: Win32_ProcessStartTrace (admin only, instant detection)
        if self._try_admin_trace(wmi):
            return

        # Strategy 2: __InstanceCreationEvent on Win32_Process (no admin needed)
        logger.info("Falling back to WMI __InstanceCreationEvent (no admin required).")
        self._run_instance_creation_watcher(wmi)

    def _try_admin_trace(self, wmi_module) -> bool:
        """
        Try using Win32_ProcessStartTrace which gives instant detection
        but requires administrator privileges. Returns True if it ran
        successfully (or until stopped), False if access was denied.
        """
        try:
            c = wmi_module.WMI()
            watcher = c.Win32_ProcessStartTrace.watch_for()
            logger.info("WMI Win32_ProcessStartTrace active (admin mode).")
        except wmi_module.x_access_denied:
            logger.info("Win32_ProcessStartTrace requires admin - not available.")
            return False
        except Exception as exc:
            logger.warning("Win32_ProcessStartTrace failed: %s", exc)
            return False

        self._run_trace_watcher(wmi_module, watcher)
        return True

    def _run_trace_watcher(self, wmi_module, watcher) -> None:
        """Event loop for Win32_ProcessStartTrace watcher."""
        while self._running.is_set():
            try:
                process = watcher(timeout_ms=1000)
                if process is None:
                    continue
                process_name = process.ProcessName.lower()
                process_pid = int(process.ProcessID)
                self._handle_new_process(process_name, process_pid)

            except wmi_module.x_wmi_timed_out:
                continue
            except Exception as exc:
                if self._running.is_set():
                    logger.error("Trace watcher error: %s", exc)
                self._running.wait(timeout=1)

    def _run_instance_creation_watcher(self, wmi_module) -> None:
        """
        Event loop using __InstanceCreationEvent on Win32_Process.
        This works without admin privileges. WMI polls internally
        at the WITHIN interval (1 second). The psutil poller provides
        faster intermediate catches every 500 ms.
        """
        while self._running.is_set():
            try:
                c = wmi_module.WMI()
                watcher = c.watch_for(
                    notification_type="Creation",
                    wmi_class="Win32_Process",
                    delay_secs=1,
                )
                logger.info("WMI __InstanceCreationEvent watcher active (standard user mode).")
            except Exception as exc:
                logger.error("Failed to start WMI instance creation watcher: %s", exc)
                self._running.wait(timeout=3)
                continue

            while self._running.is_set():
                try:
                    new_process = watcher(timeout_ms=1500)
                    if new_process is None:
                        continue
                    process_name = (new_process.Name or "").lower()
                    process_pid = int(new_process.ProcessId)
                    self._handle_new_process(process_name, process_pid)

                except wmi_module.x_wmi_timed_out:
                    continue
                except Exception as exc:
                    if self._running.is_set():
                        logger.error("Instance creation watcher error: %s -- reconnecting.", exc)
                    break  # break inner loop to reinitialize the watcher

    def _startup_scan(self) -> None:
        """
        Kill any protected apps that were already running when Nive'secureAppLock
        started. WMI only fires for *new* process creations, so without this
        scan a WhatsApp background process that was already alive would never
        be intercepted.
        """
        protected_names = self._config.get_all_process_names()
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                pname = (proc.info["name"] or "").lower()
                if pname not in protected_names:
                    continue
                pid = proc.info["pid"]
                app = self._find_matching_app(pname, pid)
                if app and app.name not in self._unlocked:
                    logger.info(
                        "Startup scan: found already-running %s (%s PID %d) - killing.",
                        app.name, pname, pid,
                    )
                    self._kill_process(app.name, pname, pid)
                    try:
                        self.app_blocked.emit(app.name)
                    except RuntimeError:
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _psutil_poll_loop(self) -> None:
        """
        Fast backup poller using psutil (500 ms intervals).
        Catches processes the WMI watcher might miss in its ~0.5-second
        detection window. Also handles the case where the WMI watcher thread
        exits unexpectedly.
        """
        _GRACE   = 10.0   # seconds after unlock before checks start
        _SUPPRESS = 2.0   # seconds after silent re-lock to suppress lock screen
        while self._running.is_set():
            now = time.monotonic()
            protected_names = self._config.get_all_process_names()
            try:
                for proc in psutil.process_iter(["name", "pid"]):
                    try:
                        pname = (proc.info["name"] or "").lower()
                        if pname not in protected_names:
                            continue
                        pid = proc.info["pid"]
                        app = self._find_matching_app(pname, pid)
                        if app and app.name not in self._unlocked:
                            # Skip if within the launch grace period
                            if now - self._unlock_times.get(app.name, 0) < _GRACE:
                                continue
                            self._kill_process(app.name, pname, pid)
                            # Suppress lock screen during background-process cleanup window
                            if now - self._silent_relock_times.get(app.name, 0) < _SUPPRESS:
                                continue
                            try:
                                self.app_blocked.emit(app.name)
                            except RuntimeError:
                                pass
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception as exc:
                logger.debug("Psutil poller error: %s", exc)
            self._running.wait(timeout=0.5)

    def _handle_new_process(self, process_name: str, process_pid: int) -> None:
        """Check if a newly created process matches a protected app."""
        protected_names = self._config.get_all_process_names()
        if process_name not in protected_names:
            return

        app = self._find_matching_app(process_name, process_pid)
        if app and app.name not in self._unlocked:
            self._kill_process(app.name, process_name, process_pid)
            # Suppress lock screen during background-process cleanup window (2 s after silent re-lock)
            if time.monotonic() - self._silent_relock_times.get(app.name, 0) < 2.0:
                return
            try:
                self.app_blocked.emit(app.name)
            except RuntimeError:
                pass

    def _kill_process(self, app_name: str, process_name: str, pid: int) -> None:
        """Kill a process and its entire tree."""
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
            logger.warning(
                "Killed %s (%s PID %d + %d children) - app is locked.",
                app_name, process_name, pid, len(children),
            )
        except psutil.NoSuchProcess:
            logger.debug("Process %s (PID %d) already gone.", process_name, pid)
        except psutil.AccessDenied as e:
            logger.error("Access denied killing %s (PID %d): %s", app_name, pid, e)
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    capture_output=True, timeout=5,
                )
                logger.info("Fallback taskkill succeeded for PID %d.", pid)
            except Exception as te:
                logger.error("Fallback taskkill failed: %s", te)
