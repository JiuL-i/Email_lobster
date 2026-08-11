"""Theme helpers: light and dark QSS palettes + a clearer default font.

Both palettes share identical class names so any QSS rule references the
same selectors regardless of the active theme.
"""

from __future__ import annotations

from PySide6.QtGui import QFont


# Palette tokens (used both inside QSS and as plain RGB tuples for QPainter).
PALETTE_LIGHT = {
    "bg": "#f1f5f9",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "border": "#e2e8f0",
    "text": "#0f172a",
    "text_muted": "#475569",
    "text_subtle": "#94a3b8",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "danger": "#dc2626",
    "success": "#16a34a",
    "warn": "#d97706",
}

PALETTE_DARK = {
    "bg": "#0b1220",
    "surface": "#111a2e",
    "surface_alt": "#0f172a",
    "border": "#1f2a44",
    "text": "#e2e8f0",
    "text_muted": "#94a3b8",
    "text_subtle": "#64748b",
    "accent": "#60a5fa",
    "accent_hover": "#93c5fd",
    "danger": "#f87171",
    "success": "#4ade80",
    "warn": "#fbbf24",
}


def _font_tokens(theme: str) -> dict[str, object]:
    common = {"family": "Segoe UI, Microsoft YaHei UI, 微软雅黑", "base_size": 10}
    if theme == "dark":
        common["mono_family"] = "Cascadia Mono, Consolas, Menlo"
        common["mono_size"] = 11
        common["title_size"] = 14
        common["value_size"] = 22
    else:
        common["mono_family"] = "Consolas, Menlo, Courier New"
        common["mono_size"] = 10
        common["title_size"] = 12
        common["value_size"] = 20
    return common


def _qss(palette: dict[str, str], font: dict[str, object]) -> str:
    return f"""
    QWidget {{
        background: {palette['bg']};
        color: {palette['text']};
        font-family: "{font['family']}";
        font-size: {font['base_size']}pt;
    }}

    /* --- top-level surfaces ------------------------------------ */
    QMainWindow, QDialog, QWizard, QWizardPage {{
        background: {palette['bg']};
    }}

    QStatusBar {{
        background: {palette['surface_alt']};
        color: {palette['text_muted']};
        border-top: 1px solid {palette['border']};
    }}

    QSplitter::handle {{
        background: {palette['border']};
    }}

    /* --- generic cards / frames --------------------------------- */
    QFrame#StatusCard, QFrame#DaemonControls {{
        background: {palette['surface']};
        border: 1px solid {palette['border']};
        border-radius: 10px;
    }}

    QFrame#StatusCard[level="ok"]      {{ border-color: {palette['success']}; }}
    QFrame#StatusCard[level="warn"]    {{ border-color: {palette['warn']}; }}
    QFrame#StatusCard[level="error"]   {{ border-color: {palette['danger']}; }}
    QFrame#StatusCard[level="info"]    {{ border-color: {palette['accent']}; }}

    QLabel#StatusCardTitle {{
        color: {palette['text_subtle']};
        font-size: 11px;
    }}

    QLabel#StatusCardValue[level="ok"]      {{ color: {palette['success']}; }}
    QLabel#StatusCardValue[level="warn"]    {{ color: {palette['warn']}; }}
    QLabel#StatusCardValue[level="error"]   {{ color: {palette['danger']}; }}
    QLabel#StatusCardValue[level="info"]    {{ color: {palette['accent']}; }}
    QLabel#StatusCardValue[level="neutral"] {{ color: {palette['text']}; }}
    QLabel#StatusCardValue {{
        font-size: {font['value_size']}pt;
        font-weight: 700;
    }}

    QLabel#StatusCardHint {{
        color: {palette['text_muted']};
        font-size: 11px;
    }}

    /* --- buttons ------------------------------------------------- */
    QPushButton {{
        background: {palette['surface']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
        padding: 6px 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {palette['surface_alt']};
        border-color: {palette['accent']};
    }}
    QPushButton:pressed {{
        background: {palette['accent']};
        color: {palette['surface']};
    }}
    QPushButton:disabled {{
        color: {palette['text_subtle']};
        background: {palette['surface_alt']};
        border-color: {palette['border']};
    }}
    QPushButton#PrimaryAction {{
        background: {palette['accent']};
        color: {palette['surface']};
        border: 1px solid {palette['accent']};
    }}
    QPushButton#PrimaryAction:hover {{
        background: {palette['accent_hover']};
        border-color: {palette['accent_hover']};
    }}

    /* --- tables -------------------------------------------------- */
    QHeaderView::section {{
        background: {palette['surface_alt']};
        color: {palette['text_muted']};
        padding: 6px 8px;
        border: 0;
        border-bottom: 1px solid {palette['border']};
        font-weight: 600;
    }}
    QTableWidget {{
        background: {palette['surface']};
        alternate-background-color: {palette['surface_alt']};
        gridline-color: {palette['border']};
        selection-background-color: {palette['accent']};
        selection-color: {palette['surface']};
    }}
    QTableWidget::item {{ padding: 4px; }}

    /* --- tabs ---------------------------------------------------- */
    QTabWidget::pane {{
        background: {palette['surface']};
        border: 1px solid {palette['border']};
        border-radius: 8px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: {palette['surface_alt']};
        color: {palette['text_muted']};
        padding: 8px 14px;
        border: 1px solid {palette['border']};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {palette['surface']};
        color: {palette['text']};
    }}
    QTabBar::tab:hover:!selected {{
        color: {palette['text']};
    }}

    /* --- log viewer (monospace) --------------------------------- */
    QPlainTextEdit#LogViewer {{
        background: {palette['surface_alt']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 8px;
        font-family: "{font['mono_family']}";
        font-size: {font['mono_size']}pt;
        selection-background-color: {palette['accent']};
        selection-color: {palette['surface']};
    }}

    /* --- dialog titles & lists ----------------------------------- */
    QLabel#DialogTitle {{
        color: {palette['text']};
        font-size: {font['title_size']}pt;
        font-weight: 700;
    }}
    QLabel#DialogSubtitle {{
        color: {palette['text_muted']};
        font-size: 11px;
    }}

    QListWidget {{
        background: {palette['surface']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 6px;
    }}

    QLineEdit, QComboBox, QSpinBox {{
        background: {palette['surface']};
        color: {palette['text']};
        border: 1px solid {palette['border']};
        border-radius: 4px;
        padding: 4px 6px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
        border-color: {palette['accent']};
    }}

    QCheckBox {{ color: {palette['text']}; }}

    QMessageBox {{
        background: {palette['surface']};
    }}
    """


def theme_qss(theme: str) -> str:
    palette = PALETTE_DARK if theme == "dark" else PALETTE_LIGHT
    return _qss(palette, _font_tokens(theme))


def default_font() -> QFont:
    font = QFont("Segoe UI")
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setPointSize(10)
    return font