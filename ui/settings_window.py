"""
Nive'secureAppLock - Settings window.
Provides UI for managing locked apps (add/remove) and changing PIN.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox,
    QLineEdit, QWidget,
)

from config.config import AppConfig, LockedApp
from auth.pin_auth import set_pin, PinError
from ui.app_picker_dialog import AppPickerDialog
from utils.logger import setup_logger

logger = setup_logger()

# -- Styling ---------------------------------------------------------------
_DIALOG_STYLE = """
    QDialog { background-color: #0d1117; }
    QLabel  { color: #c9d1d9; border: none; }
    QListWidget {
        background-color: #161b22; color: #c9d1d9;
        border: 1px solid #30363d; border-radius: 8px;
        padding: 4px; font-size: 13px;
    }
    QListWidget::item { padding: 8px; border-radius: 4px; }
    QListWidget::item:selected { background-color: #58a6ff; color: white; }
    QLineEdit {
        background-color: #161b22; color: #c9d1d9;
        border: 2px solid #30363d; border-radius: 8px;
        padding: 8px; font-size: 14px;
    }
    QLineEdit:focus { border-color: #58a6ff; }
    QPushButton {
        background-color: #238636; color: white;
        border: none; border-radius: 8px;
        padding: 10px 16px; font-size: 12px; font-weight: bold;
    }
    QPushButton:hover { background-color: #2ea043; }
"""

_REMOVE_BTN_STYLE = """
    QPushButton {
        background-color: #da3633; color: white;
        border: none; border-radius: 8px;
        padding: 10px 16px; font-size: 12px; font-weight: bold;
    }
    QPushButton:hover { background-color: #f85149; }
"""


class ChangePinDialog(QDialog):
    """Dialog to set or change the PIN."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nive'secureAppLock - Set PIN")
        self.setFixedSize(340, 240)
        self.setStyleSheet(_DIALOG_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        label = QLabel("Enter a new 4-6 digit PIN:")
        label.setFont(QFont("Segoe UI", 12))
        layout.addWidget(label)

        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin_input.setMaxLength(6)
        self.pin_input.setPlaceholderText("New PIN")
        layout.addWidget(self.pin_input)

        self.confirm_input = QLineEdit()
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_input.setMaxLength(6)
        self.confirm_input.setPlaceholderText("Confirm PIN")
        layout.addWidget(self.confirm_input)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #f85149;")
        layout.addWidget(self.status)

        save_btn = QPushButton("Save PIN")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

        self.new_hash: str | None = None

    def _save(self):
        pin = self.pin_input.text().strip()
        confirm = self.confirm_input.text().strip()
        if pin != confirm:
            self.status.setText("PINs do not match.")
            return
        try:
            self.new_hash = set_pin(pin)
            self.accept()
        except PinError as e:
            self.status.setText(str(e))


class SettingsWindow(QDialog):
    """
    Settings dialog for managing locked applications.

    Signals
    -------
    apps_changed()
        Emitted when the locked apps list is modified.
    pin_changed(str)
        Emitted with new PIN hash.
    """

    apps_changed = pyqtSignal()
    pin_changed = pyqtSignal(str)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Nive'secureAppLock - Settings")
        self.setFixedSize(500, 520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(_DIALOG_STYLE)
        self._init_ui()
        self._refresh_app_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title
        title = QLabel("Settings")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #58a6ff;")
        layout.addWidget(title)

        # -- Locked Apps Section -------------------------------------------
        apps_label = QLabel("Locked Applications:")
        apps_label.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        layout.addWidget(apps_label)

        self._app_list = QListWidget()
        self._app_list.setMinimumHeight(200)
        layout.addWidget(self._app_list)

        # Buttons row
        btn_row = QHBoxLayout()

        add_btn = QPushButton("+ Add App to Lock")
        add_btn.clicked.connect(self._add_app)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setStyleSheet(_REMOVE_BTN_STYLE)
        remove_btn.clicked.connect(self._remove_app)
        btn_row.addWidget(remove_btn)

        layout.addLayout(btn_row)

        # -- PIN Section ---------------------------------------------------
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #30363d;")
        layout.addWidget(sep)

        pin_label = QLabel("Security:")
        pin_label.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        layout.addWidget(pin_label)

        change_pin_btn = QPushButton("Change PIN")
        change_pin_btn.clicked.connect(self._change_pin)
        layout.addWidget(change_pin_btn)

        # Close
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #30363d; color: #c9d1d9;
                border: none; border-radius: 8px;
                padding: 10px 16px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #484f58; }
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def _refresh_app_list(self):
        self._app_list.clear()
        for app in self._config.locked_apps:
            processes = ", ".join(app.process_names)
            item = QListWidgetItem(f"{app.name}  ({processes})")
            item.setData(Qt.ItemDataRole.UserRole, app.name)
            self._app_list.addItem(item)

    def _add_app(self):
        """Open universal app picker to select an app to lock."""
        already_locked = self._config.get_all_process_names()
        picker = AppPickerDialog(already_locked, parent=self)
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
        self._refresh_app_list()
        self.apps_changed.emit()
        logger.info("Added app to lock list: %s (%s)", app.name, app.process_names)

    def _remove_app(self):
        """Remove the selected app from the locked list."""
        current = self._app_list.currentItem()
        if not current:
            QMessageBox.information(self, "No Selection", "Select an app to remove.")
            return

        app_name = current.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "Remove App",
            f"Remove {app_name} from locked apps?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._config.remove_app(app_name)
            self._refresh_app_list()
            self.apps_changed.emit()
            logger.info("Removed app from lock list: %s", app_name)

    def _change_pin(self):
        dlg = ChangePinDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.new_hash:
            self._config.pin_hash = dlg.new_hash
            self._config.save()
            self.pin_changed.emit(dlg.new_hash)
            QMessageBox.information(self, "PIN Updated", "Your PIN has been changed.")
