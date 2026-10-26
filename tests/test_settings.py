"""Settings file and save-folder prefill. Offscreen Qt, no Preply session."""

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

from PySide6.QtWidgets import QApplication, QDialog

from preply_extractor.app_settings import AppSettings, load_settings, save_settings, settings_path
from preply_extractor.gui import (
    ExportWorker,
    MainWindow,
    SavePanel,
    WordListPanel,
    default_save_folder,
    russian_error,
)
from preply_extractor.gui_logic import BrowserInstallError
from preply_extractor.gui_logic import format_profile
from preply_extractor.settings_dialog import SettingsDialog
from preply_extractor.ui_text import profile_text, set_language
from preply_extractor.words import VocabWord


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class SettingsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        set_language("ru")

    def tearDown(self) -> None:
        set_language("ru")

    def test_path_uses_appdata(self) -> None:
        with patch.dict(os.environ, {"APPDATA": r"C:\Users\example\AppData\Roaming"}, clear=False):
            self.assertEqual(
                settings_path(),
                Path(r"C:\Users\example\AppData\Roaming") / "PreplyExtractor" / "settings.json",
            )

    def test_roundtrip_language_and_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            saved = AppSettings(ui_language="en", default_save_dir=r"D:\Колоды\Anki")
            save_settings(saved, path)
            self.assertEqual(load_settings(path), saved)
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                body,
                {
                    "ui_language": "en",
                    "default_save_dir": r"D:\Колоды\Anki",
                    "theme": "navy",
                },
            )

    def test_missing_file_is_russian_and_empty_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-settings.json"
            self.assertEqual(load_settings(missing), AppSettings(ui_language="ru", default_save_dir=""))

    def test_corrupt_json_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            for text in ("{", "", "null", "[", "not json"):
                path.write_text(text, encoding="utf-8")
                self.assertEqual(load_settings(path), AppSettings())

    def test_unknown_language_falls_back_without_a_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps({"ui_language": "de", "default_save_dir": r"C:\keep"}),
                encoding="utf-8",
            )
            loaded = load_settings(path)
            self.assertEqual(loaded.ui_language, "ru")
            self.assertEqual(loaded.default_save_dir, "")

    def test_missing_language_key_keeps_a_string_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"default_save_dir": r"C:\decks"}), encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(loaded.ui_language, "ru")
            self.assertEqual(loaded.default_save_dir, r"C:\decks")


class SaveFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        set_language("ru")
        _app()

    def tearDown(self) -> None:
        set_language("ru")

    def test_unset_or_missing_folder_keeps_the_current_default(self) -> None:
        panel = SavePanel()
        try:
            fallback = str(default_save_folder())
            self.assertEqual(panel.folder_edit.text(), fallback)
            panel.apply_default_folder("")
            self.assertEqual(panel.folder_edit.text(), fallback)
            missing = Path(tempfile.gettempdir()) / "preply-settings-missing-folder"
            self.assertFalse(missing.exists())
            panel.apply_default_folder(str(missing))
            self.assertFalse(missing.exists())
            self.assertEqual(panel.folder_edit.text(), fallback)
        finally:
            panel.close()

    def test_existing_stored_folder_is_prefilled(self) -> None:
        panel = SavePanel()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                panel.apply_default_folder(tmp)
                self.assertEqual(Path(panel.folder_edit.text()), Path(tmp))
        finally:
            panel.close()


class SettingsWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        set_language("ru")
        _app()

    def tearDown(self) -> None:
        set_language("ru")

    def _window(self, path: Path) -> MainWindow:
        return MainWindow(settings_file=path)

    def test_button_stays_outside_the_pages_and_language_applies_on_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            folder = Path(tmp) / "decks"
            folder.mkdir()
            with patch.object(ExportWorker, "start", lambda self, *args, **kwargs: None):
                window = self._window(path)
                window.show()
                try:
                    self.assertEqual(window.windowTitle(), "Словарь Preply в Anki")
                    self.assertEqual(window.settings_button.text(), "Настройки")
                    self.assertFalse(window.stack.isAncestorOf(window.settings_button))
                    for index in range(window.stack.count()):
                        window._show_step(index)
                        self.assertTrue(window.settings_button.isVisible())
                        self.assertFalse(window.stack.isAncestorOf(window.settings_button))

                    dialog = SettingsDialog(window, load_settings(path))
                    self.assertEqual(dialog.windowTitle(), "Настройки")
                    dialog.en_radio.setChecked(True)
                    self.assertEqual(dialog.windowTitle(), "Settings")
                    self.assertEqual(dialog.ok_button.text(), "OK")
                    self.assertEqual(dialog.browse_button.text(), "Browse…")
                    self.assertEqual(dialog.ru_radio.text(), "Russian")
                    self.assertEqual(window.windowTitle(), "Словарь Preply в Anki")
                    dialog.folder_edit.setText(str(folder))
                    dialog.cancel_button.click()
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
                    self.assertEqual(load_settings(path), AppSettings())
                    self.assertEqual(window.settings_button.text(), "Настройки")

                    dialog = SettingsDialog(window, AppSettings())
                    dialog.en_radio.setChecked(True)
                    dialog.folder_edit.setText(str(folder))
                    dialog.ok_button.click()
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                    self.assertEqual(dialog.chosen(), AppSettings("en", str(folder)))
                    window._commit_settings(dialog.chosen())
                    self.assertEqual(load_settings(path), AppSettings("en", str(folder)))
                    self.assertEqual(window.windowTitle(), "Preply vocabulary to Anki")
                    self.assertEqual(window.settings_button.text(), "Settings")
                    self.assertEqual(window.login_panel.authorize_button.text(), "Authorize on Preply")
                    self.assertIn("Components", window.step_labels[0].text())
                    self.assertIn("Sign in", window.step_labels[1].text())
                    self.assertEqual(Path(window.save_panel.folder_edit.text()), folder)

                    other = Path(tmp) / "other"
                    other.mkdir()
                    save_settings(AppSettings("en", str(other)), path)
                    window._show_step(window.stack.indexOf(window.save_panel))
                    self.assertEqual(Path(window.save_panel.folder_edit.text()), other)

                    save_settings(AppSettings("en", ""), path)
                    window._show_step(0)
                    window._show_step(window.stack.indexOf(window.save_panel))
                    self.assertEqual(window.save_panel.folder_edit.text(), str(default_save_folder()))
                finally:
                    window.close()

    def test_retranslate_keeps_word_checks(self) -> None:
        panel = WordListPanel()
        try:
            panel.set_language("Spanish", "spanish", True)
            panel.set_words(
                [
                    VocabWord(id="speak", lexeme="speak", definition="to say words", examples=[]),
                    VocabWord(id="learn", lexeme="learn", definition="", examples=[]),
                ],
                keep_checks=False,
            )
            panel.select_all()
            set_language("en")
            panel.retranslate()
            self.assertEqual([word.lexeme for word in panel.selected_words()], ["speak", "learn"])
            self.assertEqual(panel.next_button.text(), "Next")
            self.assertEqual(panel.select_all_button.text(), "Select all")
            self.assertIn("no definition", panel.word_list.item(1).text())
            self.assertFalse(panel.switch_button.isHidden())
        finally:
            panel.close()

    def test_russian_profile_text_matches_the_existing_helper(self) -> None:
        set_language("ru")
        self.assertEqual(profile_text("42", "Ada Lovelace"), format_profile("42", "Ada Lovelace"))
        self.assertEqual(profile_text("7", None), format_profile("7", None))
        set_language("en")
        self.assertEqual(profile_text("42", "Ada Lovelace"), "ID: 42\nName: Ada Lovelace")
        self.assertEqual(profile_text("7", None), "ID: 7\nName: not in the response")

    def test_browser_error_stays_russian_until_the_language_changes(self) -> None:
        set_language("ru")
        russian = russian_error(BrowserInstallError(1, "network down\nplaywright install chromium"))
        self.assertIn("Повторить", russian)
        self.assertIn("network down", russian)
        self.assertNotIn("playwright install", russian.lower())
        set_language("en")
        english = russian_error(BrowserInstallError(1, "network down\nplaywright install chromium"))
        self.assertIn("Retry", english)
        self.assertIn("network down", english)
        self.assertNotIn("playwright install", english.lower())
        self.assertNotIn("Повторить", english)
