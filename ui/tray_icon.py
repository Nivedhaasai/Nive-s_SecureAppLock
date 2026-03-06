"""
Nive'secureAppLock - System tray icon.
Provides context menu for Settings, Add App to Lock, Change PIN, and Exit.
Exit requires authentication (fingerprint or PIN) to prevent bypass.
"""

import ctypes
import threading
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QApplication, QDialog, QVBoxLayout,
    QLabel, QLineEdit, QPushButton, QWidget,
)

from config.config import AppConfig
from auth.fingerprint_auth import authenticate_windows_hello, is_windows_hello_available
from auth.pin_auth import verify_pin
from utils.startup import enable_startup, disable_startup, is_startup_enabled
from utils.logger import setup_logger

logger = setup_logger()

# -- Colour constants (match lock_screen theme) ----------------------------
BG      = "#0d1117"
CARD_BG = "#161b22"
ACCENT  = "#58a6ff"
SUCCESS = "#3fb950"
ERROR   = "#f85149"
TEXT    = "#c9d1d9"
SUBTEXT = "#8b949e"
BORDER  = "#30363d"


class AuthGateDialog(QDialog):
    """
    Authentication dialog for gating protected actions (Exit, Settings).
    Requires fingerprint or PIN before allowing the action to proceed.
    """

    _fp_finished = pyqtSignal(bool)  # thread-safe fingerprint result

    def __init__(self, pin_hash: str, fingerprint_enabled: bool = True,
                 title: str = "Authenticate to Continue",
                 subtitle: str = "Verify your identity to proceed.",
                 parent=None):
        super().__init__(parent)
        self._pin_hash = pin_hash
        self._fingerprint_enabled = fingerprint_enabled
        self._fp_available = fingerprint_enabled and is_windows_hello_available()
        self._title_text = title
        self._subtitle_text = subtitle
        self._fp_in_progress = False
        self._fp_finished.connect(self._fp_result)
        self.setWindowTitle("Nive'secureAppLock - Authentication Required")
        self.setFixedSize(380, 340)
        self.setWindowFlags(
            self.windowFlags()
            | self.windowFlags().__class__.WindowStaysOnTopHint
        )
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG}; }}
            QLabel  {{ color: {TEXT}; border: none; }}
        """)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 28, 28, 28)

        # Icon + title
        icon = QLabel("\U0001F6E1")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI Emoji", 32))
        layout.addWidget(icon)

        title = QLabel(self._title_text)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(title)

        subtitle = QLabel(self._subtitle_text)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setFont(QFont("Segoe UI", 10))
        subtitle.setStyleSheet(f"color: {SUBTEXT};")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER};")
        layout.addWidget(sep)

        # Fingerprint button
        if self._fp_available:
            self._fp_btn = QPushButton("Unlock with Fingerprint")
            self._fp_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
            self._fp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._fp_btn.setMinimumHeight(44)
            self._fp_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ACCENT}; color: #ffffff;
                    border: none; border-radius: 10px; padding: 10px 16px;
                }}
                QPushButton:hover {{ background-color: #79c0ff; }}
            """)
            self._fp_btn.clicked.connect(self._on_fingerprint)
            layout.addWidget(self._fp_btn)

        # PIN input
        self._pin_input = QLineEdit()
        self._pin_input.setPlaceholderText("Enter PIN")
        self._pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin_input.setMaxLength(6)
        self._pin_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pin_input.setFont(QFont("Segoe UI", 14))
        self._pin_input.setMinimumHeight(44)
        self._pin_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {CARD_BG}; color: {TEXT};
                border: 2px solid {BORDER}; border-radius: 10px;
                padding: 8px 14px; letter-spacing: 6px;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        self._pin_input.returnPressed.connect(self._on_pin_submit)
        layout.addWidget(self._pin_input)

        # PIN submit button
        pin_btn = QPushButton("Confirm with PIN")
        pin_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pin_btn.setMinimumHeight(44)
        pin_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #da3633; color: #ffffff;
                border: none; border-radius: 10px; padding: 10px 16px;
            }}
            QPushButton:hover {{ background-color: #f85149; }}
        """)
        pin_btn.clicked.connect(self._on_pin_submit)
        layout.addWidget(pin_btn)

        # Status
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFont(QFont("Segoe UI", 10))
        self._status.setStyleSheet(f"color: {SUBTEXT};")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def _on_fingerprint(self):
        """Trigger Windows Hello authentication for exit."""
        if self._fp_in_progress:
            return
        self._fp_in_progress = True
        self._status.setText("Waiting for Windows Hello ...")
        self._status.setStyleSheet(f"color: {ACCENT};")
        QApplication.processEvents()

        # Make the dialog invisible and lower its z-order so the native
        # Windows Security dialog gets full foreground focus.
        # NOTE: We must NOT call self.hide() because that would terminate
        # the exec() event loop and cause the caller to see Rejected.
        self.setWindowOpacity(0)
        self._lower_z_order()

        thread = threading.Thread(target=self._fp_worker, daemon=True)
        thread.start()

    def _lower_z_order(self):
        """Push this dialog behind other windows via Win32 API."""
        try:
            hwnd = int(self.winId())
            HWND_BOTTOM = 1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def _raise_z_order(self):
        """Bring this dialog back to the top via Win32 API."""
        try:
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE,
            )
        except Exception:
            pass

        thread = threading.Thread(target=self._fp_worker, daemon=True)
        thread.start()

    def _fp_worker(self):
        success = authenticate_windows_hello()
        self._fp_finished.emit(success)

    def _fp_result(self, success: bool):
        self._fp_in_progress = False
        if success:
            logger.info("Auth gate: fingerprint successful.")
            self.setWindowOpacity(1)
            self.accept()
        else:
            # Restore the dialog for PIN fallback
            self.setWindowOpacity(1)
            self._raise_z_order()
            self.raise_()
            self.activateWindow()
            self._status.setText("Fingerprint failed. Use PIN instead.")
            self._status.setStyleSheet(f"color: {ERROR};")

    def _on_pin_submit(self):
        pin = self._pin_input.text().strip()
        if not pin:
            self._status.setText("Enter your PIN.")
            self._status.setStyleSheet(f"color: {ERROR};")
            return

        if verify_pin(pin, self._pin_hash):
            logger.info("Exit authentication successful (PIN).")
            self.accept()
        else:
            self._status.setText("Incorrect PIN.")
            self._status.setStyleSheet(f"color: {ERROR};")
            self._pin_input.clear()


class TrayIcon(QSystemTrayIcon):
    """
    System tray icon with context menu.
    Exit requires fingerprint/PIN authentication.

    Signals
    -------
    settings_requested()
    add_app_requested()
    pin_change_requested()
    quit_authenticated()
        Emitted only after the user successfully authenticates to exit.
    """

    settings_requested = pyqtSignal()
    add_app_requested = pyqtSignal()
    pin_change_requested = pyqtSignal()
    quit_authenticated = pyqtSignal()

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self._config = config
        self._build_menu()

        self.setIcon(QApplication.style().standardIcon(
            QApplication.style().StandardPixmap.SP_ComputerIcon
        ))
        self.setToolTip("Nive'secureAppLock - Running")
        self.activated.connect(self._on_activated)
        self.show()

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #161b22; color: #c9d1d9;
                border: 1px solid #30363d; border-radius: 6px;
                padding: 4px;
            }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #58a6ff; color: white; }
            QMenu::separator { background-color: #30363d; height: 1px; margin: 4px 8px; }
        """)

        # Settings
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.settings_requested.emit)
        menu.addAction(settings_action)

        menu.addSeparator()

        # Add App to Lock
        add_action = QAction("Add App to Lock", self)
        add_action.triggered.connect(self.add_app_requested.emit)
        menu.addAction(add_action)

        # Change PIN
        pin_action = QAction("Change PIN", self)
        pin_action.triggered.connect(self.pin_change_requested.emit)
        menu.addAction(pin_action)

        menu.addSeparator()

        # Auto-start toggle
        self._startup_action = QAction("", self)
        self._update_startup_label()
        self._startup_action.triggered.connect(self._toggle_startup)
        menu.addAction(self._startup_action)

        menu.addSeparator()

        # Exit (requires authentication)
        quit_action = QAction("Exit (Requires Auth)", self)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings_requested.emit()

    def _toggle_startup(self):
        if is_startup_enabled():
            disable_startup()
            self._config.auto_start = False
        else:
            enable_startup()
            self._config.auto_start = True
        self._config.save()
        self._update_startup_label()

    def _update_startup_label(self):
        enabled = is_startup_enabled()
        self._startup_action.setText(
            "Disable Auto-Start" if enabled else "Enable Auto-Start"
        )

    def _on_quit(self):
        """
        Show authentication dialog before allowing exit.
        Only emits quit_authenticated if the user successfully authenticates.
        """
        logger.info("User requested exit - authentication required.")
        dlg = AuthGateDialog(
            title="Authenticate to Exit",
            subtitle="Verify your identity to stop Nive'secureAppLock protection.",
            pin_hash=self._config.pin_hash,
            fingerprint_enabled=self._config.fingerprint_enabled,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            logger.info("Exit authorized by user.")
            self.quit_authenticated.emit()
        else:
            logger.info("Exit cancelled - authentication failed or dismissed.")
            self.showMessage(
                "Nive'secureAppLock",
                "Exit denied. Authentication required to stop protection.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
