"""
Nive'secureAppLock — First-run setup dialog.
Prompts the user to create their initial PIN before locking begins.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
)

from auth.pin_auth import set_pin, PinError


class SetupDialog(QDialog):
    """
    Shown on first run when no PIN is configured.
    The user must set a PIN to continue.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nive'secureAppLock — Initial Setup")
        self.setFixedSize(400, 340)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("""
            QDialog { background-color: #0d1117; }
            QLabel  { color: #c9d1d9; }
            QLineEdit {
                background-color: #161b22; color: #c9d1d9;
                border: 2px solid #30363d; border-radius: 10px;
                padding: 10px; font-size: 16px; letter-spacing: 6px;
            }
            QLineEdit:focus { border-color: #58a6ff; }
            QPushButton {
                background-color: #238636; color: white;
                border: none; border-radius: 10px;
                padding: 12px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #2ea043; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(36, 36, 36, 36)

        # Icon
        icon = QLabel("🔐")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI Emoji", 36))
        layout.addWidget(icon)

        # Title
        title = QLabel("Welcome to Nive'secureAppLock")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #58a6ff;")
        layout.addWidget(title)

        # Subtitle
        sub = QLabel("Create a backup PIN (4–6 digits) to get started.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Segoe UI", 11))
        sub.setStyleSheet("color: #8b949e;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        # PIN input
        self._pin = QLineEdit()
        self._pin.setEchoMode(QLineEdit.EchoMode.Password)
        self._pin.setMaxLength(6)
        self._pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pin.setPlaceholderText("Enter PIN")
        layout.addWidget(self._pin)

        # Confirm
        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm.setMaxLength(6)
        self._confirm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._confirm.setPlaceholderText("Confirm PIN")
        layout.addWidget(self._confirm)

        # Error label
        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error.setStyleSheet("color: #f85149; font-size: 11px;")
        layout.addWidget(self._error)

        # Button
        btn = QPushButton("🚀  Start Protecting")
        btn.clicked.connect(self._submit)
        layout.addWidget(btn)

        self.result_hash: str | None = None

    def _submit(self):
        pin = self._pin.text().strip()
        confirm = self._confirm.text().strip()

        if pin != confirm:
            self._error.setText("PINs do not match.")
            return
        try:
            self.result_hash = set_pin(pin)
            self.accept()
        except PinError as e:
            self._error.setText(str(e))

    def closeEvent(self, event):
        """Prevent closing without setting a PIN."""
        if self.result_hash is None:
            event.ignore()
        else:
            super().closeEvent(event)
