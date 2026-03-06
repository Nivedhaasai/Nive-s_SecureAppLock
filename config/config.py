"""
Nive'secureAppLock - Configuration manager.
Loads, validates, and persists settings in config.json.
Supports universal app locking with both Store apps and desktop EXEs.
"""

import json
import os
from dataclasses import dataclass, field, asdict

_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")


@dataclass
class LockedApp:
    """Represents a locked application."""
    name: str
    process_names: list[str]       # e.g. ["WhatsApp.exe", "WhatsApp.Root.exe"]
    launch_command: str            # shell:AppsFolder\... URI or path to EXE
    is_store_app: bool = True      # True for UWP/Store apps, False for desktop EXEs


@dataclass
class AppConfig:
    """Application configuration."""
    pin_hash: str = ""
    fingerprint_enabled: bool = True
    auto_start: bool = True
    locked_apps: list[LockedApp] = field(default_factory=list)

    def __post_init__(self):
        normalised = []
        for app in self.locked_apps:
            if isinstance(app, dict):
                normalised.append(LockedApp(**app))
            else:
                normalised.append(app)
        self.locked_apps = normalised

    @classmethod
    def load(cls) -> "AppConfig":
        """Load config from disk, or return defaults."""
        if os.path.exists(_CONFIG_FILE):
            try:
                with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls(**data)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return cls._defaults()

    def save(self) -> None:
        """Persist config to disk."""
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    def get_all_process_names(self) -> set[str]:
        """Return a lowercase set of all protected process names."""
        names = set()
        for app in self.locked_apps:
            for pn in app.process_names:
                names.add(pn.lower())
        return names

    def find_app_by_process(self, process_name: str) -> "LockedApp | None":
        """Find a LockedApp by one of its process names (case-insensitive)."""
        proc_lower = process_name.lower()
        for app in self.locked_apps:
            for pn in app.process_names:
                if pn.lower() == proc_lower:
                    return app
        return None

    def find_app_by_name(self, name: str) -> "LockedApp | None":
        """Find a LockedApp by display name."""
        for app in self.locked_apps:
            if app.name == name:
                return app
        return None

    def add_app(self, app: LockedApp) -> None:
        """Add a new application to the locked list and save."""
        self.locked_apps.append(app)
        self.save()

    def remove_app(self, name: str) -> bool:
        """Remove an application by name. Returns True if found."""
        for i, app in enumerate(self.locked_apps):
            if app.name == name:
                self.locked_apps.pop(i)
                self.save()
                return True
        return False

    @classmethod
    def _defaults(cls) -> "AppConfig":
        """Return a config with the default locked apps."""
        return cls(
            pin_hash="",
            fingerprint_enabled=True,
            auto_start=False,
            locked_apps=[
                LockedApp(
                    name="WhatsApp",
                    process_names=["WhatsApp.Root.exe", "WhatsApp.exe"],
                    launch_command=r"shell:AppsFolder\5319275A.WhatsAppDesktop_cw5n1h2txyewy!App",
                    is_store_app=True,
                ),
                LockedApp(
                    name="Instagram",
                    process_names=["msedge.exe"],
                    launch_command=r"shell:AppsFolder\Facebook.InstagramBeta_8xx8rvfyw5nnt!App",
                    is_store_app=True,
                ),
            ],
        )
