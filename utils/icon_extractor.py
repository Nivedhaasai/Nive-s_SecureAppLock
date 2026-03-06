"""
Nive'secureAppLock - App icon extraction utility.

Extracts application icons for the lock screen using multiple strategies:
  1. QFileIconProvider on the exe path  (desktop apps)
  2. Start-Menu shortcut search         (UWP and desktop)
  3. shutil.which lookup                (PATH-accessible exe)
  4. Coloured-initial badge fallback    (always works)
"""

import glob
import os
import shutil

from PyQt6.QtCore import Qt, QFileInfo, QRect
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QPen
from PyQt6.QtWidgets import QFileIconProvider

from utils.logger import setup_logger

logger = setup_logger()

# ── Icon cache ────────────────────────────────────────────────────────────
_cache: dict[str, QPixmap] = {}

# ── Brand colours for well-known apps ─────────────────────────────────────
_BRAND_COLORS: dict[str, str] = {
    "whatsapp":   "#25D366",
    "instagram":  "#E4405F",
    "telegram":   "#2AABEE",
    "facebook":   "#1877F2",
    "messenger":  "#00B2FF",
    "twitter":    "#1DA1F2",
    "tiktok":     "#010101",
    "snapchat":   "#FFFC00",
    "discord":    "#5865F2",
    "spotify":    "#1DB954",
    "slack":      "#4A154B",
    "teams":      "#6264A7",
    "zoom":       "#2D8CFF",
    "chrome":     "#4285F4",
    "firefox":    "#FF7139",
    "edge":       "#0078D7",
    "opera":      "#FF1B2D",
    "brave":      "#FB542B",
    "steam":      "#1B2838",
    "outlook":    "#0078D4",
    "word":       "#2B579A",
    "excel":      "#217346",
    "powerpoint": "#D24726",
    "onenote":    "#7719AA",
    "vscode":     "#007ACC",
    "github":     "#333333",
    "skype":      "#00AFF0",
    "signal":     "#3A76F0",
    "pinterest":  "#E60023",
    "reddit":     "#FF5700",
    "linkedin":   "#0A66C2",
    "youtube":    "#FF0000",
    "netflix":    "#E50914",
    "vlc":        "#FF8800",
}

_PALETTE = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#DDA0DD",
    "#FF9FF3", "#54A0FF", "#5F27CD", "#FF6348", "#1ABC9C",
    "#E74C3C", "#3498DB", "#9B59B6", "#F39C12", "#2ECC71",
]


# ── Public API ────────────────────────────────────────────────────────────

def get_app_icon(
    app_name: str,
    process_names: list[str],
    launch_command: str,
    is_store_app: bool,
    size: int = 72,
) -> QPixmap:
    """
    Return a *size × size* QPixmap icon for the given application.

    Tries real icon extraction first; falls back to a coloured-initial
    badge that uses the app's brand colour (if known) or a deterministic
    palette colour.
    """
    if app_name in _cache:
        cached = _cache[app_name]
        if cached.width() == size and cached.height() == size:
            return cached
        return cached.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    icon = _try_extract(app_name, process_names, launch_command, is_store_app, size)
    if icon is None:
        icon = _create_initial_badge(app_name, size)

    _cache[app_name] = icon
    return icon


# ── Extraction strategies ─────────────────────────────────────────────────

def _try_extract(
    app_name: str,
    process_names: list[str],
    launch_command: str,
    is_store_app: bool,
    size: int,
) -> QPixmap | None:
    provider = QFileIconProvider()

    # Strategy 1 — direct exe path (desktop apps)
    if not is_store_app and os.path.isfile(launch_command):
        pm = provider.icon(QFileInfo(launch_command)).pixmap(size, size)
        if _is_usable(pm):
            logger.debug("Icon from exe path for %s", app_name)
            return pm

    # Strategy 2 — Start-Menu shortcuts
    for start_dir in _start_menu_dirs():
        try:
            for match in glob.glob(
                os.path.join(start_dir, "**", f"*{app_name}*"),
                recursive=True,
            ):
                if match.lower().endswith((".lnk", ".url")):
                    pm = provider.icon(QFileInfo(match)).pixmap(size, size)
                    if _is_usable(pm):
                        logger.debug("Icon from shortcut for %s: %s", app_name, match)
                        return pm
        except OSError:
            continue

    # Strategy 3 — shutil.which
    for pname in process_names:
        path = shutil.which(pname)
        if path:
            pm = provider.icon(QFileInfo(path)).pixmap(size, size)
            if _is_usable(pm):
                logger.debug("Icon via which() for %s: %s", app_name, path)
                return pm

    return None


# ── Helpers ───────────────────────────────────────────────────────────────

def _is_usable(pm: QPixmap) -> bool:
    return not pm.isNull() and pm.width() >= 16 and pm.height() >= 16


def _start_menu_dirs() -> list[str]:
    dirs: list[str] = []
    for env in ("APPDATA", "ProgramData"):
        base = os.environ.get(env, "")
        if base:
            d = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")
            if os.path.isdir(d):
                dirs.append(d)
    return dirs


def _get_color(app_name: str) -> str:
    lower = app_name.lower()
    for key, colour in _BRAND_COLORS.items():
        if key in lower:
            return colour
    return _PALETTE[hash(app_name) % len(_PALETTE)]


def _create_initial_badge(app_name: str, size: int) -> QPixmap:
    """Coloured circle with the app's first letter."""
    colour = _get_color(app_name)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Circle
    p.setBrush(QColor(colour))
    p.setPen(QPen(QColor(colour).darker(120), 2))
    margin = 2
    p.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)

    # Letter
    p.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", int(size * 0.38), QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(
        QRect(0, 0, size, size),
        Qt.AlignmentFlag.AlignCenter,
        app_name[0].upper() if app_name else "?",
    )

    p.end()
    return pm
