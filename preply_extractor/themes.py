"""Dark navy appearance for the desktop wizard, plus an unused light analog.

Styles cover the login screen, language list, word list, settings dialog,
deck build, and save step. The running window always uses ``navy``.
``light_navy_stylesheet`` is the same layout with a light background.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette

THEME_IDS: tuple[str, ...] = ("navy",)
DEFAULT_THEME = "navy"


@dataclass(frozen=True)
class ThemeColors:
    background: str
    surface: str
    card: str
    border: str
    text: str
    muted: str
    accent: str
    accent_strong: str
    accent_hover: str
    on_accent: str
    on_bright: str
    connected: str
    logging_in: str
    installing: str
    failed: str
    done: str
    radius: str
    button_padding: str
    item_padding: str


_NAVY = ThemeColors(
    background="#0b1c2e",
    surface="#10263a",
    card="#143044",
    border="#1e4d63",
    text="#e7f4f6",
    muted="#8eb4c4",
    accent="#2dd4bf",
    accent_strong="#0f766e",
    accent_hover="#14b8a6",
    on_accent="#f4fffd",
    on_bright="#042f2e",
    connected="#5eead4",
    logging_in="#fde68a",
    installing="#67e8f9",
    failed="#fda4af",
    done="#6ee7b7",
    radius="10px",
    button_padding="8px 16px",
    item_padding="8px 6px",
)

# Same turquoise accent and geometry as navy, with a light page and dark text.
_NAVY_LIGHT = ThemeColors(
    background="#f3fbfb",
    surface="#ffffff",
    card="#ffffff",
    border="#b7d5d2",
    text="#102433",
    muted="#3d5c66",
    accent="#2dd4bf",
    accent_strong="#0f766e",
    accent_hover="#14b8a6",
    on_accent="#f4fffd",
    on_bright="#042f2e",
    connected="#0f766e",
    logging_in="#a16207",
    installing="#0e7490",
    failed="#be123c",
    done="#047857",
    radius="10px",
    button_padding="8px 16px",
    item_padding="8px 6px",
)

THEMES: dict[str, ThemeColors] = {
    "navy": _NAVY,
}

_CSS = """
QWidget {
    font-family: "Segoe UI";
    color: __TEXT__;
}
QMainWindow, QWidget#surface {
    background-color: transparent;
    color: __TEXT__;
}
QDialog, QMessageBox {
    background-color: __BACKGROUND__;
    color: __TEXT__;
}
QStackedWidget, QStackedWidget > QWidget {
    background: transparent;
}
QLabel {
    background: transparent;
    color: __TEXT__;
}
QLabel#title {
    font-size: 20px;
    font-weight: 600;
    color: __TEXT__;
}
QLabel#detail, QLabel#themeCaption {
    color: __MUTED__;
}
QLabel#connection[state="connected"] { color: __CONNECTED__; font-weight: 600; }
QLabel#connection[state="checking"] { color: __INSTALLING__; font-weight: 600; }
QLabel#connection[state="ready"] { color: __CONNECTED__; font-weight: 600; }
QLabel#connection[state="logging_in"] { color: __LOGGING_IN__; font-weight: 600; }
QLabel#connection[state="installing"] { color: __INSTALLING__; font-weight: 600; }
QLabel#connection[state="failed"] { color: __FAILED__; font-weight: 600; }
QLabel#step { color: __MUTED__; padding: 4px 2px; background: transparent; }
QLabel#step[active="true"] { color: __ACCENT__; font-weight: 600; }
QLabel#step[done="true"] { color: __DONE__; }
QListWidget {
    background: __CARD__;
    color: __TEXT__;
    border: 1px solid __BORDER__;
    border-radius: __RADIUS__;
    padding: 6px;
    outline: none;
    alternate-background-color: __SURFACE__;
}
QListWidget::item {
    padding: __ITEM_PADDING__;
    border-radius: 4px;
}
QListWidget::item:hover { background: __SURFACE__; }
QListWidget::item:selected {
    background: __ACCENT_STRONG__;
    color: __ON_ACCENT__;
}
QListWidget::indicator, QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid __ACCENT__;
    background: __CARD__;
}
QListWidget::indicator:checked, QCheckBox::indicator:checked {
    background: __ACCENT__;
    border: 1px solid __ACCENT__;
}
QLineEdit, QComboBox {
    background: __CARD__;
    color: __TEXT__;
    border: 1px solid __BORDER__;
    border-radius: __RADIUS__;
    padding: 6px 10px;
    min-height: 28px;
    selection-background-color: __ACCENT_STRONG__;
    selection-color: __ON_ACCENT__;
}
QLineEdit:focus, QComboBox:focus, QListWidget:focus {
    border-color: __ACCENT__;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
}
QComboBox QAbstractItemView {
    background: __CARD__;
    color: __TEXT__;
    selection-background-color: __ACCENT_STRONG__;
    selection-color: __ON_ACCENT__;
    border: 1px solid __BORDER__;
    outline: 0;
}
QPushButton {
    background: __CARD__;
    color: __TEXT__;
    border: 1px solid __BORDER__;
    border-radius: __RADIUS__;
    padding: __BUTTON_PADDING__;
    min-height: 32px;
}
QPushButton:hover {
    background: __SURFACE__;
    border-color: __ACCENT__;
}
QPushButton:disabled {
    color: __MUTED__;
    background: __SURFACE__;
    border-color: __BORDER__;
}
QPushButton#primary {
    background: __ACCENT_STRONG__;
    color: __ON_ACCENT__;
    border: none;
    border-radius: __RADIUS__;
    padding: __BUTTON_PADDING__;
    font-weight: 600;
}
QPushButton#primary:hover { background: __ACCENT_HOVER__; }
QPushButton#primary:disabled {
    background: __BORDER__;
    color: __MUTED__;
}
QPushButton#windowButton, QPushButton#closeButton, QPushButton#settingsButton {
    background: transparent;
    color: __TEXT__;
    border: none;
    border-radius: 6px;
    padding: 0;
    margin: 0;
    min-width: 36px;
    max-width: 36px;
    min-height: 32px;
    max-height: 32px;
    font-size: 14px;
}
QPushButton#windowButton:hover, QPushButton#settingsButton:hover { background: __SURFACE__; }
QPushButton#closeButton:hover { background: #be123c; color: #ffffff; }
QRadioButton {
    color: __TEXT__;
    background: transparent;
    spacing: 8px;
    padding: 2px 0;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 2px solid __BORDER__;
    background: __CARD__;
}
QRadioButton::indicator:checked {
    border: 2px solid __ACCENT__;
    background: __ACCENT__;
}
QProgressBar {
    border: 1px solid __BORDER__;
    border-radius: __RADIUS__;
    background: __CARD__;
    color: __TEXT__;
    text-align: center;
    min-height: 22px;
}
QProgressBar::chunk {
    background: __ACCENT__;
    border-radius: 4px;
}
QScrollBar:vertical {
    background: __BACKGROUND__;
    width: 12px;
    margin: 0;
    border-radius: 6px;
}
QScrollBar::handle:vertical {
    background: __BORDER__;
    border-radius: 6px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: __ACCENT__; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: __BACKGROUND__;
    height: 12px;
    margin: 0;
    border-radius: 6px;
}
QScrollBar::handle:horizontal {
    background: __BORDER__;
    border-radius: 6px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: __ACCENT__; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QToolTip {
    background: __CARD__;
    color: __TEXT__;
    border: 1px solid __ACCENT__;
    padding: 4px 8px;
}
QMessageBox QLabel { color: __TEXT__; background: transparent; }
"""


def normalize_theme(value: object) -> str:
    """Ignore any saved or requested id. The window only uses navy."""
    del value
    return DEFAULT_THEME


def accent_color(theme_id: str) -> str:
    del theme_id
    return _NAVY.accent


def _render(theme: ThemeColors) -> str:
    css = _CSS
    replacements = {
        "__BACKGROUND__": theme.background,
        "__SURFACE__": theme.surface,
        "__CARD__": theme.card,
        "__BORDER__": theme.border,
        "__TEXT__": theme.text,
        "__MUTED__": theme.muted,
        "__ACCENT_STRONG__": theme.accent_strong,
        "__ACCENT_HOVER__": theme.accent_hover,
        "__ACCENT__": theme.accent,
        "__ON_ACCENT__": theme.on_accent,
        "__ON_BRIGHT__": theme.on_bright,
        "__CONNECTED__": theme.connected,
        "__LOGGING_IN__": theme.logging_in,
        "__INSTALLING__": theme.installing,
        "__FAILED__": theme.failed,
        "__DONE__": theme.done,
        "__RADIUS__": theme.radius,
        "__BUTTON_PADDING__": theme.button_padding,
        "__ITEM_PADDING__": theme.item_padding,
    }
    for token, value in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        css = css.replace(token, value)
    return css


def stylesheet(theme_id: str = DEFAULT_THEME) -> str:
    """Dark navy sheet. Any other id is ignored."""
    del theme_id
    return _render(_NAVY)


def light_navy_stylesheet() -> str:
    """Light counterpart of navy. Not applied by the running window."""
    return _render(_NAVY_LIGHT)


def apply_theme(app, theme_id: str = DEFAULT_THEME) -> str:
    """Restyle the whole application with dark navy, ignoring ``theme_id``."""
    del theme_id
    if app is None:
        return DEFAULT_THEME
    app.setStyle("Fusion")
    app.setPalette(_palette(_NAVY))
    app.setStyleSheet(stylesheet())
    return DEFAULT_THEME


def _palette(theme: ThemeColors) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: theme.background,
        QPalette.ColorRole.WindowText: theme.text,
        QPalette.ColorRole.Base: theme.card,
        QPalette.ColorRole.AlternateBase: theme.surface,
        QPalette.ColorRole.Text: theme.text,
        QPalette.ColorRole.Button: theme.card,
        QPalette.ColorRole.ButtonText: theme.text,
        QPalette.ColorRole.Highlight: theme.accent_strong,
        QPalette.ColorRole.HighlightedText: theme.on_accent,
        QPalette.ColorRole.ToolTipBase: theme.card,
        QPalette.ColorRole.ToolTipText: theme.text,
        QPalette.ColorRole.PlaceholderText: theme.muted,
        QPalette.ColorRole.Link: theme.accent,
        QPalette.ColorRole.Mid: theme.border,
        QPalette.ColorRole.Dark: theme.background,
        QPalette.ColorRole.Shadow: theme.background,
        QPalette.ColorRole.Light: theme.surface,
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, color in roles.items():
            palette.setColor(group, role, QColor(color))
    disabled = dict(roles)
    disabled[QPalette.ColorRole.WindowText] = theme.muted
    disabled[QPalette.ColorRole.Text] = theme.muted
    disabled[QPalette.ColorRole.ButtonText] = theme.muted
    for role, color in disabled.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    return palette
