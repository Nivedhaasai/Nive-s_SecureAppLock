"""
Nive'secureAppLock - Universal app picker dialog.
Searchable dialog that lists all installed Store + desktop apps for locking.
"""

import threading
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem,
)

from utils.app_discovery import DiscoveredApp, discover_all_apps
from utils.logger import setup_logger

logger = setup_logger()

# -- Styling ---------------------------------------------------------------
_STYLE = """
    QDialog { background-color: #0d1117; }
    QLabel  { color: #c9d1d9; border: none; }
    QLineEdit {
        background-color: #161b22; color: #c9d1d9;
        border: 2px solid #30363d; border-radius: 8px;
        padding: 10px 14px; font-size: 14px;
    }
    QLineEdit:focus { border-color: #58a6ff; }
    QListWidget {
        background-color: #161b22; color: #c9d1d9;
        border: 1px solid #30363d; border-radius: 8px;
        padding: 4px; font-size: 13px;
    }
    QListWidget::item { padding: 10px 8px; border-radius: 4px; }
    QListWidget::item:selected { background-color: #58a6ff; color: white; }
    QListWidget::item:hover { background-color: #21262d; }
"""


class AppPickerDialog(QDialog):
    """
    Universal app picker — shows all installed Store + desktop apps
    in a searchable list. User selects one to add to the lock list.

    Attributes
    ----------
    selected_app : DiscoveredApp | None
        Set after accepting with a valid selection.
    """

    _discovery_done = pyqtSignal(list)  # thread-safe discovery result

    def __init__(self, already_locked: set[str], parent=None):
        """
        Parameters
        ----------
        already_locked : set[str]
            Lowercase process names already in the lock list (for dedup).
        """
        super().__init__(parent)
        self._already_locked = already_locked
        self._all_apps: list[DiscoveredApp] = []
        self.selected_app: DiscoveredApp | None = None

        self.setWindowTitle("Nive'secureAppLock - Add App to Lock")
        self.setFixedSize(520, 600)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(_STYLE)
        self._discovery_done.connect(self._on_discovery_done)
        self._init_ui()
        self._start_discovery()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title
        title = QLabel("Select an App to Lock")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #58a6ff;")
        layout.addWidget(title)

        subtitle = QLabel("All installed Store and desktop applications:")
        subtitle.setFont(QFont("Segoe UI", 10))
        subtitle.setStyleSheet("color: #8b949e;")
        layout.addWidget(subtitle)

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search apps...")
        self._search.textChanged.connect(self._filter_list)
        layout.addWidget(self._search)

        # App list
        self._list = QListWidget()
        self._list.setMinimumHeight(380)
        self._list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self._list)

        # Status label (shows during loading)
        self._status = QLabel("Scanning installed apps...")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFont(QFont("Segoe UI", 10))
        self._status.setStyleSheet("color: #8b949e;")
        layout.addWidget(self._status)

        # Buttons row
        btn_row = QHBoxLayout()

        self._add_btn = QPushButton("Lock Selected App")
        self._add_btn.setEnabled(False)
        self._add_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self._add_btn.setMinimumHeight(42)
        self._add_btn.setStyleSheet("""
            QPushButton {
                background-color: #238636; color: white;
                border: none; border-radius: 8px;
                padding: 10px 16px; font-weight: bold;
            }
            QPushButton:hover { background-color: #2ea043; }
            QPushButton:disabled { background-color: #21262d; color: #484f58; }
        """)
        self._add_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._add_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFont(QFont("Segoe UI", 12))
        cancel_btn.setMinimumHeight(42)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #30363d; color: #c9d1d9;
                border: none; border-radius: 8px;
                padding: 10px 16px;
            }
            QPushButton:hover { background-color: #484f58; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

        # Enable add button when selection changes
        self._list.currentItemChanged.connect(self._on_selection_changed)

    def _start_discovery(self):
        """Run app discovery in a background thread."""
        thread = threading.Thread(target=self._discover_worker, daemon=True)
        thread.start()

    def _discover_worker(self):
        apps = discover_all_apps()
        self._discovery_done.emit(apps)

    def _on_discovery_done(self, apps: list[DiscoveredApp]):
        self._all_apps = apps
        self._populate_list(apps)
        count = self._list.count()
        self._status.setText(f"{count} apps found")
        if count > 0:
            self._search.setFocus()

    def _populate_list(self, apps: list[DiscoveredApp]):
        self._list.clear()
        for app in apps:
            # Skip already locked apps
            app_procs = {p.lower() for p in app.process_names}
            if app_procs & self._already_locked:
                continue

            tag = "Store" if app.is_store_app else "Desktop"
            procs = ", ".join(app.process_names) if app.process_names else "unknown"
            text = f"{app.name}   [{tag}]   ({procs})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, app)
            self._list.addItem(item)

    def _filter_list(self, text: str):
        query = text.strip().lower()
        if not query:
            self._populate_list(self._all_apps)
            return

        filtered = [
            app for app in self._all_apps
            if query in app.name.lower()
            or any(query in p.lower() for p in app.process_names)
        ]
        self._populate_list(filtered)

    def _on_selection_changed(self, current, _previous):
        self._add_btn.setEnabled(current is not None)

    def _on_accept(self):
        current = self._list.currentItem()
        if not current:
            return
        self.selected_app = current.data(Qt.ItemDataRole.UserRole)
        self.accept()
