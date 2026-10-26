"""Theme styles and the settings switch. Offscreen Qt, no Preply session."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from preply_extractor.app_settings import AppSettings, load_settings, save_settings
from preply_extractor.gui import ExportWorker, MainWindow
from preply_extractor.settings_dialog import SettingsDialog
from preply_extractor.themes import THEME_IDS, accent_color, apply_theme, normalize_theme, stylesheet
from preply_extractor.ui_text import set_language, tr

_ACCENTS = {"navy": "#2dd4bf", "violet": "#a78bfa", "amber": "#f59e0b"}
_BACKGROUNDS = {"navy": "#0b1c2e", "violet": "#0a0612", "amber": "#1c1916"}
_PADDING = {"navy": "8px 16px", "violet": "5px 12px", "amber": "10px 20px"}


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class ThemeStyleTests(unittest.TestCase):
    def test_three_themes_exist_and_differ(self) -> None:
        self.assertEqual(THEME_IDS, ("navy", "violet", "amber"))
        sheets = {theme_id: stylesheet(theme_id) for theme_id in THEME_IDS}
        self.assertEqual(len(set(sheets)), 3)
        for theme_id, sheet in sheets.items():
            self.assertTrue(sheet.strip())
            self.assertNotIn("__", sheet)
            self.assertEqual(accent_color(theme_id), _ACCENTS[theme_id])
            self.assertIn(_ACCENTS[theme_id], sheet)
            self.assertIn(_BACKGROUNDS[theme_id], sheet)
            self.assertIn(_PADDING[theme_id], sheet)
            self.assertIn("Segoe UI", sheet)
            self.assertIn("QDialog", sheet)
            self.assertIn("QListWidget", sheet)
            self.assertIn("QProgressBar", sheet)
            self.assertIn("QPushButton#primary", sheet)
            self.assertIn("::indicator", sheet)
            for other_id, other_accent in _ACCENTS.items():
                if other_id != theme_id:
                    self.assertNotIn(other_accent, sheet)
                    self.assertNotIn(_BACKGROUNDS[other_id], sheet)

    def test_unknown_theme_falls_back_to_navy(self) -> None:
        self.assertEqual(normalize_theme(None), "navy")
        self.assertEqual(normalize_theme(""), "navy")
        self.assertEqual(normalize_theme("neon"), "navy")
        self.assertEqual(normalize_theme(" VIOLET "), "violet")
        self.assertEqual(stylesheet("nope"), stylesheet("navy"))

    def test_apply_theme_updates_the_applied_id(self) -> None:
        app = _app()
        self.assertEqual(apply_theme(app, "amber"), "amber")
        self.assertIn(_ACCENTS["amber"], app.styleSheet())
        self.assertNotIn(_ACCENTS["violet"], app.styleSheet())
        self.assertEqual(apply_theme(app, "nope"), "navy")
        self.assertIn(_ACCENTS["navy"], app.styleSheet())
        self.assertEqual(apply_theme(app, "violet"), "violet")
        self.assertIn(_ACCENTS["violet"], app.styleSheet())


class ThemeSettingsTests(unittest.TestCase):
    def test_round_trip_stores_theme_with_language_and_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            saved = AppSettings(ui_language="en", default_save_dir=r"D:\Колоды\Anki", theme="violet")
            save_settings(saved, path)
            self.assertEqual(load_settings(path), saved)
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                body,
                {
                    "ui_language": "en",
                    "default_save_dir": r"D:\Колоды\Anki",
                    "theme": "violet",
                },
            )

    def test_missing_or_invalid_theme_keeps_the_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps({"ui_language": "en", "default_save_dir": r"C:\decks"}),
                encoding="utf-8",
            )
            loaded = load_settings(path)
            self.assertEqual(loaded, AppSettings("en", r"C:\decks", "navy"))

            path.write_text(
                json.dumps({"ui_language": "ru", "default_save_dir": r"D:\x", "theme": "neon"}),
                encoding="utf-8",
            )
            loaded = load_settings(path)
            self.assertEqual(loaded.ui_language, "ru")
            self.assertEqual(loaded.default_save_dir, r"D:\x")
            self.assertEqual(loaded.theme, "navy")

            path.write_text(
                json.dumps({"ui_language": "en", "default_save_dir": r"C:\decks", "theme": 3}),
                encoding="utf-8",
            )
            self.assertEqual(load_settings(path).theme, "navy")
            self.assertEqual(load_settings(path).default_save_dir, r"C:\decks")


class ThemeDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        set_language("ru")
        _app()

    def tearDown(self) -> None:
        set_language("ru")

    def test_labels_follow_the_dialog_language(self) -> None:
        dialog = SettingsDialog(None, AppSettings())
        try:
            self.assertEqual(dialog.theme_caption.text(), "Оформление")
            self.assertEqual(dialog.theme_buttons["navy"].text(), "Синий")
            self.assertEqual(dialog.theme_buttons["violet"].text(), "Фиолетовый")
            self.assertEqual(dialog.theme_buttons["amber"].text(), "Янтарный")
            self.assertTrue(dialog.theme_buttons["navy"].isChecked())
            dialog.en_radio.setChecked(True)
            self.assertEqual(dialog.theme_caption.text(), "Theme")
            self.assertEqual(dialog.theme_buttons["navy"].text(), "Navy")
            self.assertEqual(dialog.theme_buttons["violet"].text(), "Violet")
            self.assertEqual(dialog.theme_buttons["amber"].text(), "Amber")
            self.assertEqual(tr("theme", language="ru"), "Оформление")
            self.assertEqual(tr("theme", language="en"), "Theme")
        finally:
            dialog.close()

    def test_cancel_restores_theme_and_ok_keeps_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            folder = Path(tmp) / "decks"
            folder.mkdir()
            save_settings(AppSettings("en", str(folder), "navy"), path)
            with patch.object(ExportWorker, "start", lambda self, *args, **kwargs: None):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    self.assertEqual(window._theme, "navy")
                    self.assertFalse(hasattr(window, "theme_combo"))
                    self.assertFalse(hasattr(window, "theme_caption"))
                    self.assertIn(_ACCENTS["navy"], QApplication.instance().styleSheet())

                    dialog = window._make_settings_dialog(load_settings(path))
                    self.assertEqual(set(dialog.theme_buttons), {"navy", "violet", "amber"})
                    self.assertTrue(dialog.theme_buttons["navy"].isChecked())
                    self.assertEqual(dialog.theme_caption.text(), "Theme")
                    dialog.theme_buttons["amber"].setChecked(True)
                    self.assertEqual(window._theme, "amber")
                    self.assertEqual(load_settings(path).theme, "amber")
                    self.assertEqual(load_settings(path).ui_language, "en")
                    self.assertEqual(load_settings(path).default_save_dir, str(folder))
                    self.assertIn(_ACCENTS["amber"], QApplication.instance().styleSheet())
                    dialog.ok_button.click()

                    dialog = window._make_settings_dialog(load_settings(path))
                    self.assertTrue(dialog.theme_buttons["amber"].isChecked())
                    self.assertEqual(dialog.theme_caption.text(), "Theme")
                    dialog.theme_buttons["violet"].setChecked(True)
                    self.assertEqual(window._theme, "violet")
                    self.assertEqual(load_settings(path).theme, "violet")
                    self.assertIn(_ACCENTS["violet"], QApplication.instance().styleSheet())
                    self.assertEqual(dialog.selected_theme(), "violet")

                    dialog.ru_radio.setChecked(True)
                    dialog.folder_edit.setText(r"C:\do-not-keep")
                    dialog.cancel_button.click()
                    restored = load_settings(path)
                    self.assertEqual(restored, AppSettings("en", str(folder), "amber"))
                    self.assertEqual(window._theme, "amber")
                    self.assertEqual(window.windowTitle(), "Preply vocabulary to Anki")
                    self.assertIn(_ACCENTS["amber"], QApplication.instance().styleSheet())
                    self.assertNotIn(_ACCENTS["violet"], QApplication.instance().styleSheet())

                    other = Path(tmp) / "other"
                    other.mkdir()
                    dialog = window._make_settings_dialog(load_settings(path))
                    dialog.theme_buttons["navy"].setChecked(True)
                    self.assertEqual(window._theme, "navy")
                    self.assertIn(_ACCENTS["navy"], QApplication.instance().styleSheet())
                    dialog.ru_radio.setChecked(True)
                    dialog.folder_edit.setText(str(other))
                    dialog.ok_button.click()
                    kept = load_settings(path)
                    self.assertEqual(kept, AppSettings("ru", str(other), "navy"))
                    self.assertEqual(window._theme, "navy")
                    self.assertEqual(window.windowTitle(), "Словарь Preply в Anki")
                    self.assertEqual(window.settings_button.text(), "Настройки")
                    self.assertIn(_ACCENTS["navy"], QApplication.instance().styleSheet())
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
