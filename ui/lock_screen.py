"""
Nive'secureAppLock - Lock screen overlay.

Mobile-app-lock style: shows the target app's icon and name.
Fingerprint (Windows Hello) is triggered automatically; the lock screen
temporarily lowers its z-order so the native Windows Security dialog is
not obscured.  PIN entry is available as a fallback.
"""

import ctypes
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QKeyEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QLineEdit, QGraphicsDropShadowEffect, QApplication,
)

from config.config import AppConfig
from auth.pin_auth import verify_pin
from auth.fingerprint_auth import authenticate_windows_hello, is_windows_hello_available
from utils.icon_extractor import get_app_icon
from utils.logger import setup_logger

logger = setup_logger()

# -- Colour palette --------------------------------------------------------
BG           = "#0d1117"
CARD_BG      = "#161b22"
ACCENT       = "#58a6ff"
ACCENT_HOVER = "#79c0ff"
SUCCESS      = "#3fb950"
ERROR        = "#f85149"
TEXT         = "#c9d1d9"
SUBTEXT      = "#8b949e"
BORDER       = "#30363d"


class LockScreen(QWidget):
    """
    Fullscreen, frameless lock-screen overlay — styled like a mobile app lock.

    Shows the target app's icon and name.  Automatically triggers Windows Hello
    fingerprint; the window's z-order is lowered while the native Windows
    Security dialog is active so it is never hidden behind the overlay.

    Signals
    -------
    authenticated(str)
        Emitted with the app name on successful authentication.
    """

    authenticated = pyqtSignal(str)
    _fp_finished = pyqtSignal(bool)  # internal: thread-safe fingerprint result

    def __init__(self, config: AppConfig, fingerprint_enabled: bool = True):
        super().__init__()
        self._config = config
        self._pin_hash = config.pin_hash
        self._fingerprint_enabled = fingerprint_enabled
        self._target_app: str = ""
        self._pending_apps: list[str] = []
        self._attempts = 0
        self._max_attempts = 10
        self._fp_in_progress = False
        self._fp_available = fingerprint_enabled and is_windows_hello_available()
        self._fp_finished.connect(self._fp_result)
        self._init_ui()

    # -- Win32 z-order control ---------------------------------------------

    def _set_topmost(self, topmost: bool) -> None:
        """Toggle always-on-top via Win32 API without recreating the window."""
        try:
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            flag = HWND_TOPMOST if topmost else HWND_NOTOPMOST
            ctypes.windll.user32.SetWindowPos(
                hwnd, flag, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    # -- UI construction ---------------------------------------------------

    def _init_ui(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background-color: {BG};")

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # -- Card container ------------------------------------------------
        card = QWidget()
        card.setFixedWidth(380)
        card.setStyleSheet(f"""
            QWidget {{
                background-color: {CARD_BG};
                border-radius: 24px;
                border: 1px solid {BORDER};
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(14)
        card_layout.setContentsMargins(36, 36, 36, 36)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(80)
        shadow.setColor(QColor(88, 166, 255, 60))
        shadow.setOffset(0, 0)
        card.setGraphicsEffect(shadow)

        # -- App icon (populated per-app in show_for_app) ------------------
        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedSize(80, 80)
        self._icon_label.setStyleSheet("border: none; background: transparent;")
        card_layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # -- App name (prominent) ------------------------------------------
        self._app_label = QLabel("")
        self._app_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._app_label.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self._app_label.setStyleSheet(f"color: {TEXT}; border: none;")
        card_layout.addWidget(self._app_label)

        # Subtitle
        self._subtitle = QLabel("Unlock to continue")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setFont(QFont("Segoe UI", 11))
        self._subtitle.setStyleSheet(f"color: {SUBTEXT}; border: none;")
        card_layout.addWidget(self._subtitle)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER}; border: none;")
        card_layout.addWidget(sep)

        # Fingerprint status
        self._fp_status = QLabel("")
        self._fp_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fp_status.setFont(QFont("Segoe UI", 12))
        self._fp_status.setStyleSheet(f"color: {ACCENT}; border: none;")
        card_layout.addWidget(self._fp_status)

        # "Use PIN instead" button
        self._use_pin_btn = QPushButton("Use PIN instead")
        self._use_pin_btn.setFont(QFont("Segoe UI", 11))
        self._use_pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_pin_btn.setMinimumHeight(42)
        self._use_pin_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {SUBTEXT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                color: {TEXT};
                border-color: {ACCENT};
            }}
        """)
        self._use_pin_btn.clicked.connect(self._show_pin_input)
        card_layout.addWidget(self._use_pin_btn)

        # -- PIN section (hidden by default) --------------------------------
        self._pin_container = QWidget()
        self._pin_container.setStyleSheet("border: none;")
        pin_layout = QVBoxLayout(self._pin_container)
        pin_layout.setSpacing(10)
        pin_layout.setContentsMargins(0, 0, 0, 0)

        self._pin_input = QLineEdit()
        self._pin_input.setPlaceholderText("Enter PIN (4-6 digits)")
        self._pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin_input.setMaxLength(6)
        self._pin_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pin_input.setFont(QFont("Segoe UI", 16))
        self._pin_input.setMinimumHeight(50)
        self._pin_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG};
                color: {TEXT};
                border: 2px solid {BORDER};
                border-radius: 12px;
                padding: 8px 16px;
                letter-spacing: 8px;
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
        """)
        self._pin_input.returnPressed.connect(self._on_pin_submit)
        pin_layout.addWidget(self._pin_input)

        self._pin_btn = QPushButton("Unlock")
        self._pin_btn.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setMinimumHeight(48)
        self._pin_btn.setStyleSheet(self._button_style("#238636", "#2ea043"))
        self._pin_btn.clicked.connect(self._on_pin_submit)
        pin_layout.addWidget(self._pin_btn)

        self._pin_container.setVisible(False)
        card_layout.addWidget(self._pin_container)

        # -- Retry fingerprint button (hidden by default) -------------------
        self._retry_fp_btn = QPushButton("Retry Fingerprint")
        self._retry_fp_btn.setFont(QFont("Segoe UI", 11))
        self._retry_fp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_fp_btn.setMinimumHeight(42)
        self._retry_fp_btn.setStyleSheet(self._button_style(ACCENT, ACCENT_HOVER))
        self._retry_fp_btn.clicked.connect(self._trigger_fingerprint)
        self._retry_fp_btn.setVisible(False)
        card_layout.addWidget(self._retry_fp_btn)

        # Status message
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFont(QFont("Segoe UI", 10))
        self._status.setStyleSheet(f"color: {SUBTEXT}; border: none;")
        self._status.setWordWrap(True)
        card_layout.addWidget(self._status)

        main_layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def _button_style(bg: str, hover: str) -> str:
        return f"""
            QPushButton {{
                background-color: {bg};
                color: #ffffff;
                border: none;
                border-radius: 12px;
                padding: 10px 20px;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:pressed {{
                background-color: {bg};
            }}
        """

    # -- public API --------------------------------------------------------

    def show_for_app(self, app_name: str) -> None:
        """Display the lock screen for a specific app."""
        # If fingerprint auth is in progress, the lock screen is deliberately
        # hidden so the Windows Security dialog is accessible.  Do NOT
        # re-show it — just queue if it's a different app.
        if self._fp_in_progress:
            if app_name != self._target_app and app_name not in self._pending_apps:
                self._pending_apps.append(app_name)
                logger.info("FP in progress for %s — queued %s", self._target_app, app_name)
            return

        # If already showing for another app, queue this one
        if self.isVisible() and self._target_app and self._target_app != app_name:
            if app_name not in self._pending_apps:
                self._pending_apps.append(app_name)
                logger.info("Lock screen busy with %s — queued %s", self._target_app, app_name)
            return

        # If already showing for the SAME app, ignore duplicate signals
        if self.isVisible() and self._target_app == app_name:
            return

        self._target_app = app_name
        self._attempts = 0
        self._app_label.setText(app_name)
        self._subtitle.setText("Unlock to continue")
        self._status.setText("")
        self._status.setStyleSheet(f"color: {SUBTEXT}; border: none;")
        self._pin_input.clear()
        self._pin_container.setVisible(False)
        self._retry_fp_btn.setVisible(False)
        self._use_pin_btn.setVisible(True)

        # Load app icon
        self._load_app_icon(app_name)

        if self._fp_available:
            # -- Enterprise fingerprint path: no fullscreen flash. -----------
            # Trigger fingerprint IMMEDIATELY. The native Windows Security
            # dialog IS the UI — it shows "Unlock <AppName>" and the
            # fingerprint sensor activates at once, just like laptop unlock.
            # Only show our fullscreen lock screen if fingerprint fails.
            self._fp_status.setText("Waiting for Windows Hello ...")
            self._fp_status.setStyleSheet(f"color: {ACCENT}; border: none;")
            logger.info("Triggering fingerprint directly for %s (no fullscreen)", app_name)
            self._trigger_fingerprint()
        else:
            # -- PIN-only path: show fullscreen immediately. ----------------
            self._fp_status.setVisible(self._fingerprint_enabled)
            if self._fingerprint_enabled:
                self._fp_status.setText("Windows Hello not available — use PIN")
                self._fp_status.setStyleSheet(f"color: {SUBTEXT}; border: none;")
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            logger.info("Lock screen shown for %s (PIN mode)", app_name)
            self._show_pin_input()

    def _load_app_icon(self, app_name: str) -> None:
        """Look up the app in config, extract its icon and display it."""
        app = self._config.find_app_by_name(app_name)
        if app:
            icon = get_app_icon(
                app.name, app.process_names,
                app.launch_command, app.is_store_app, size=72,
            )
        else:
            icon = get_app_icon(app_name, [], "", False, size=72)
        scaled = icon.scaled(
            72, 72,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._icon_label.setPixmap(scaled)

    def update_pin_hash(self, new_hash: str) -> None:
        self._pin_hash = new_hash

    # -- event overrides ---------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            return
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
            return
        if event.key() == Qt.Key.Key_F4 and (event.modifiers() & Qt.KeyboardModifier.AltModifier):
            event.ignore()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        event.ignore()

    # -- authentication handlers -------------------------------------------

    def _trigger_fingerprint(self) -> None:
        """Start Windows Hello authentication in a background thread."""
        if self._fp_in_progress:
            return
        self._fp_in_progress = True
        self._retry_fp_btn.setVisible(False)

        # If retrying from fullscreen, hide the overlay so the native
        # Windows Security dialog appears on a clean desktop.
        if self.isVisible():
            self.hide()

        thread = threading.Thread(target=self._fp_worker, daemon=True)
        thread.start()

    def _fp_worker(self) -> None:
        """Background thread — calls the C# service with the app name."""
        success = authenticate_windows_hello(self._target_app)

        # Signal is thread-safe — reliably delivers to main thread
        # even when the widget is hidden.
        self._fp_finished.emit(success)

    def _fp_result(self, success: bool) -> None:
        """Handle fingerprint result on the main thread."""
        self._fp_in_progress = False
        if success:
            # Unlock immediately — no lock screen was shown (or it was hidden).
            self._on_success()
        else:
            # Fingerprint failed or timed out — NOW show the fullscreen
            # lock screen with retry + PIN options.
            self._attempts += 1
            self._fp_status.setVisible(True)
            self._fp_status.setText("Fingerprint failed — try again or use PIN")
            self._fp_status.setStyleSheet(f"color: {ERROR}; border: none;")
            self._retry_fp_btn.setVisible(True)
            self._show_pin_input()
            self._load_app_icon(self._target_app)
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            logger.info("Fingerprint failed for %s — showing lock screen with PIN", self._target_app)

    def _show_pin_input(self) -> None:
        """Reveal the PIN input section."""
        self._pin_container.setVisible(True)
        self._use_pin_btn.setVisible(False)
        self._pin_input.setFocus()

    def _on_pin_submit(self) -> None:
        pin = self._pin_input.text().strip()
        if not pin:
            self._show_error("Please enter your PIN")
            return

        if not self._pin_hash:
            self._show_error("No PIN configured")
            return

        if verify_pin(pin, self._pin_hash):
            self._on_success()
        else:
            self._attempts += 1
            remaining = self._max_attempts - self._attempts
            if remaining > 0:
                self._show_error(f"Incorrect PIN - {remaining} attempts remaining")
            else:
                self._show_error("Too many incorrect attempts. Please wait.")
                self._pin_btn.setEnabled(False)
                self._pin_input.setEnabled(False)
                QTimer.singleShot(30000, self._reset_lockout)

        self._pin_input.clear()

    def _on_success(self) -> None:
        logger.info("Authentication successful for %s", self._target_app)
        if self.isVisible():
            self._status.setText("\u2713  Unlocked")
            self._status.setStyleSheet(f"color: {SUCCESS}; border: none; font-weight: bold;")
            QTimer.singleShot(400, self._finish_unlock)
        else:
            # Lock screen is already hidden (fingerprint path) — finish immediately
            self._finish_unlock()

    def _finish_unlock(self) -> None:
        self.hide()
        self._fp_in_progress = False
        self.authenticated.emit(self._target_app)
        # If there are pending apps queued, show lock screen for the next one
        if self._pending_apps:
            next_app = self._pending_apps.pop(0)
            QTimer.singleShot(300, lambda: self.show_for_app(next_app))

    def _show_error(self, msg: str) -> None:
        self._status.setText(msg)
        self._status.setStyleSheet(f"color: {ERROR}; border: none;")

    def _reset_lockout(self) -> None:
        self._attempts = 0
        self._pin_btn.setEnabled(True)
        self._pin_input.setEnabled(True)
        self._status.setText("Lockout cleared - try again.")
        self._status.setStyleSheet(f"color: {SUBTEXT}; border: none;")
