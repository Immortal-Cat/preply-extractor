"""Navy theme and the settings dialog. Offscreen Qt, no Preply session."""

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

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from preply_extractor.app_settings import AppSettings, load_settings, save_settings
from preply_extractor.gui import ExportWorker, MainWindow
from preply_extractor.settings_dialog import SettingsDialog
from preply_extractor.themes import (
    accent_color,
    apply_theme,
    light_navy_stylesheet,
    normalize_theme,
    stylesheet,
)
from preply_extractor.ui_text import set_language

_NAVY_ACCENT = "#2dd4bf"
_NAVY_BACKGROUND = "#0b1c2e"
_LIGHT_BACKGROUND = "#f3fbfb"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class ThemeStyleTests(unittest.TestCase):
    def test_only_navy_is_applied_and_light_analog_differs(self) -> None:
        dark = stylesheet("violet")
        light = light_navy_stylesheet()
        self.assertEqual(dark, stylesheet("navy"))
        self.assertTrue(dark.strip())
        self.assertNotIn("__", dark)
        self.assertNotIn("__", light)
        self.assertEqual(accent_color("amber"), _NAVY_ACCENT)
        self.assertIn(_NAVY_ACCENT, dark)
        self.assertIn(_NAVY_ACCENT, light)
        self.assertIn(_NAVY_BACKGROUND, dark)
        self.assertIn(f"background-color: {_LIGHT_BACKGROUND}", light)
        self.assertNotEqual(dark, light)
        self.assertNotIn("#a78bfa", dark)
        self.assertNotIn("#f59e0b", dark)
        for sheet in (dark, light):
            self.assertIn("8px 16px", sheet)
            self.assertIn("Segoe UI", sheet)
            self.assertIn("QDialog", sheet)
            self.assertIn("QListWidget", sheet)
            self.assertIn("QProgressBar", sheet)
            self.assertIn("QPushButton#primary", sheet)
            self.assertIn("::indicator", sheet)

    def test_saved_theme_names_are_ignored(self) -> None:
        self.assertEqual(normalize_theme(None), "navy")
        self.assertEqual(normalize_theme(""), "navy")
        self.assertEqual(normalize_theme("neon"), "navy")
        self.assertEqual(normalize_theme(" VIOLET "), "navy")
        self.assertEqual(normalize_theme(3), "navy")
        self.assertEqual(stylesheet("nope"), stylesheet("navy"))

    def test_apply_theme_stays_on_dark_navy(self) -> None:
        app = _app()
        self.assertEqual(apply_theme(app, "amber"), "navy")
        self.assertIn(_NAVY_ACCENT, app.styleSheet())
        self.assertIn(_NAVY_BACKGROUND, app.styleSheet())
        self.assertNotIn("#a78bfa", app.styleSheet())
        self.assertNotIn("#f59e0b", app.styleSheet())
        self.assertEqual(apply_theme(app, "violet"), "navy")
        self.assertIn(_NAVY_BACKGROUND, app.styleSheet())


class ThemeSettingsTests(unittest.TestCase):
    def test_round_trip_keeps_language_and_folder_and_ignores_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AppSettings(ui_language="en", default_save_dir=r"D:\Колоды\Anki", theme="violet"), path)
            loaded = load_settings(path)
            self.assertEqual(loaded.ui_language, "en")
            self.assertEqual(loaded.default_save_dir, r"D:\Колоды\Anki")
            self.assertEqual(loaded.theme, "navy")
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["theme"], "navy")
            self.assertEqual(body["ui_language"], "en")

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

    def test_settings_dialog_has_no_theme_selector(self) -> None:
        dialog = SettingsDialog(None, AppSettings(theme="violet"))
        try:
            self.assertFalse(hasattr(dialog, "theme_buttons"))
            self.assertFalse(hasattr(dialog, "theme_caption"))
            self.assertFalse(dialog.findChildren(QPushButton, "themeChoice"))
            labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
            self.assertNotIn("Оформление", labels)
            self.assertNotIn("Theme", labels)
            self.assertIn("Язык интерфейса", labels)
            dialog.en_radio.setChecked(True)
            labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
            self.assertIn("Interface language", labels)
            self.assertNotIn("Theme", labels)
            self.assertEqual(dialog.chosen().theme, "violet")
        finally:
            dialog.close()

    def test_startup_uses_dark_navy_even_when_settings_name_another_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            folder = Path(tmp) / "decks"
            folder.mkdir()
            path.write_text(
                json.dumps({"ui_language": "en", "default_save_dir": str(folder), "theme": "violet"}),
                encoding="utf-8",
            )
            with patch.object(ExportWorker, "start", lambda self, *args, **kwargs: None):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    sheet = QApplication.instance().styleSheet()
                    self.assertEqual(window._theme, "navy")
                    self.assertFalse(hasattr(window, "theme_combo"))
                    self.assertFalse(hasattr(window, "theme_caption"))
                    self.assertIn(_NAVY_ACCENT, sheet)
                    self.assertIn(_NAVY_BACKGROUND, sheet)
                    self.assertNotIn("#a78bfa", sheet)
                    self.assertNotIn("#f59e0b", sheet)

                    dialog = window._make_settings_dialog(load_settings(path))
                    self.assertFalse(hasattr(dialog, "theme_buttons"))
                    dialog.ru_radio.setChecked(True)
                    dialog.folder_edit.setText(r"C:\do-not-keep")
                    dialog.cancel_button.click()
                    restored = load_settings(path)
                    self.assertEqual(restored.ui_language, "en")
                    self.assertEqual(restored.default_save_dir, str(folder))
                    self.assertEqual(restored.theme, "navy")
                    self.assertEqual(window.windowTitle(), "Preply vocabulary to Anki")
                    self.assertIn(_NAVY_BACKGROUND, QApplication.instance().styleSheet())

                    other = Path(tmp) / "other"
                    other.mkdir()
                    dialog = window._make_settings_dialog(load_settings(path))
                    dialog.ru_radio.setChecked(True)
                    dialog.folder_edit.setText(str(other))
                    dialog.ok_button.click()
                    kept = load_settings(path)
                    self.assertEqual(kept, AppSettings("ru", str(other), "navy"))
                    self.assertEqual(window._theme, "navy")
                    self.assertEqual(window.windowTitle(), "Словарь Preply в Anki")
                    self.assertEqual(window.settings_button.text(), "Настройки")
                    self.assertIn(_NAVY_ACCENT, QApplication.instance().styleSheet())
                    self.assertIn(_NAVY_BACKGROUND, QApplication.instance().styleSheet())
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
