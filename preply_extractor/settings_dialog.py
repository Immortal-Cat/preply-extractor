"""Application-modal settings dialog for language and the default deck folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from preply_extractor.ui_text import tr


class SettingsDialog(QDialog):
    """Edits language and folder locally. The caller saves them on OK.

    Cancel leaves both as they were. There is no theme control.
    """

    def __init__(self, parent, settings: AppSettings) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.setMinimumWidth(560)
        self._kept_theme = settings.theme

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
        layout.addWidget(self.folder_caption)
        layout.addLayout(folder_row)
        layout.addLayout(buttons)
        self._apply_dialog_language()

    def chosen(self) -> AppSettings:
        language = "en" if self.en_radio.isChecked() else "ru"
        return AppSettings(
            ui_language=language,
            default_save_dir=self.folder_edit.text().strip(),
            theme=self._kept_theme,
        )

    def _dialog_language(self) -> str:
        return "en" if self.en_radio.isChecked() else "ru"

    def _on_language_toggled(self, checked: bool) -> None:
        if checked:
            self._apply_dialog_language()

    def _apply_dialog_language(self) -> None:
        language = self._dialog_language()
        self.setWindowTitle(tr("settings", language=language))
        self.language_caption.setText(tr("interface_language", language=language))
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
