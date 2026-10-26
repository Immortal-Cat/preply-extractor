"""Application-modal settings dialog for language, theme, and the default deck folder."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from preply_extractor.app_settings import AppSettings
from preply_extractor.themes import THEME_IDS, normalize_theme
from preply_extractor.ui_text import tr


class SettingsDialog(QDialog):
    """Edits language and folder locally. Theme changes call ``on_theme_preview`` immediately.

    The caller saves language and folder on OK. Cancel leaves those two as they were.
    Theme preview is the caller's job: apply it live, and roll it back if the dialog
    is rejected.
    """

    def __init__(
        self,
        parent,
        settings: AppSettings,
        on_theme_preview: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.setMinimumWidth(560)
        self._theme = normalize_theme(settings.theme)
        self._on_theme_preview = on_theme_preview

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self.language_caption = QLabel()
        self.ru_radio = QRadioButton()
        self.en_radio = QRadioButton()
        self.ru_radio.setChecked(settings.ui_language != "en")
        self.en_radio.setChecked(settings.ui_language == "en")
        self.ru_radio.toggled.connect(self._on_language_toggled)
        self.en_radio.toggled.connect(self._on_language_toggled)

        self.theme_caption = QLabel()
        self.theme_buttons: dict[str, QPushButton] = {}
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        for theme_id in THEME_IDS:
            button = QPushButton()
            button.setObjectName("themeChoice")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.theme_buttons[theme_id] = button
            self._theme_group.addButton(button)
            theme_row.addWidget(button, 1)
        self.theme_buttons[self._theme].setChecked(True)
        for theme_id, button in self.theme_buttons.items():
            button.toggled.connect(
                lambda checked, chosen=theme_id: self._on_theme_toggled(chosen, checked)
            )

        self.folder_caption = QLabel()
        self.folder_caption.setWordWrap(True)
        self.folder_edit = QLineEdit(settings.default_save_dir)
        self.folder_edit.setClearButtonEnabled(True)
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton()
        self.ok_button = QPushButton()
        self.ok_button.setObjectName("primary")
        self.ok_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)

        layout.addWidget(self.language_caption)
        layout.addWidget(self.ru_radio)
        layout.addWidget(self.en_radio)
        layout.addWidget(self.theme_caption)
        layout.addLayout(theme_row)
        layout.addWidget(self.folder_caption)
        layout.addLayout(folder_row)
        layout.addLayout(buttons)
        self._apply_dialog_language()

    def selected_theme(self) -> str:
        return self._theme

    def chosen(self) -> AppSettings:
        language = "en" if self.en_radio.isChecked() else "ru"
        return AppSettings(
            ui_language=language,
            default_save_dir=self.folder_edit.text().strip(),
            theme=self.selected_theme(),
        )

    def _dialog_language(self) -> str:
        return "en" if self.en_radio.isChecked() else "ru"

    def _on_language_toggled(self, checked: bool) -> None:
        if checked:
            self._apply_dialog_language()

    def _on_theme_toggled(self, theme_id: str, checked: bool) -> None:
        if not checked or theme_id == self._theme:
            return
        self._theme = theme_id
        preview = self._on_theme_preview
        if preview is not None:
            preview(theme_id)

    def _apply_dialog_language(self) -> None:
        language = self._dialog_language()
        self.setWindowTitle(tr("settings", language=language))
        self.language_caption.setText(tr("interface_language", language=language))
        self.theme_caption.setText(tr("theme", language=language))
        for theme_id, button in self.theme_buttons.items():
            button.setText(tr(f"theme_{theme_id}", language=language))
        self.folder_caption.setText(tr("default_folder", language=language))
        self.browse_button.setText(tr("browse", language=language))
        self.ok_button.setText(tr("done", language=language))
        self.cancel_button.setText(tr("cancel", language=language))
        self.ru_radio.blockSignals(True)
        self.en_radio.blockSignals(True)
        self.ru_radio.setText(tr("language_name_ru", language=language))
        self.en_radio.setText(tr("language_name_en", language=language))
        self.ru_radio.blockSignals(False)
        self.en_radio.blockSignals(False)

    def _browse(self) -> None:
        language = self._dialog_language()
        current = self.folder_edit.text().strip()
        start = current if current and Path(current).is_dir() else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("choose_deck_folder", language=language), start)
        if chosen:
            self.folder_edit.setText(chosen)
