"""Wizard selection and save-path checks. No Preply session required."""

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

from preply_extractor.gui import (
    PAGE_SIZE,
    UI_LANGUAGE,
    ExportWorker,
    LanguagePanel,
    LoginPanel,
    MainWindow,
    WordListPanel,
    _STEP_DEPS,
    _STEP_LOGIN,
    _STEP_SAVE,
    russian_error,
)
from preply_extractor.gui_logic import (
    BrowserInstallError,
    ensure_playwright_chromium,
    format_profile,
    install_chromium_command,
    install_commands_for,
    install_playwright_package_command,
    join_deck_path,
    missing_browser_message,
    prepare_playwright_runtime,
    vocabulary_alias,
)
from preply_extractor.deck_job import open_browser_session
from preply_extractor.preply_api import (
    CATEGORIES,
    BrowserSession,
    PreplyError,
    _payload,
    fetch_vocabulary,
    parse_learner_profile,
    preferred_browser_channel,
)
from preply_extractor.words import VocabWord


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _words() -> list[VocabWord]:
    return [
        VocabWord(id="speak", lexeme="speak", definition="to say words, to use the voice", examples=[]),
        VocabWord(id="learn", lexeme="learn", definition="to get knowledge or skill by studying", examples=[]),
    ]


class _FakeLoginWindow:
    def __init__(self, missing_session: bool = False) -> None:
        self.is_open = True
        self.closed = False
        self.missing_session = missing_session

    def read_preply_cookies(self) -> list[dict]:
        if self.missing_session:
            from preply_extractor.browser_login import NoPreplySession

            raise NoPreplySession("no preply.com session cookie")
        return [{"name": "sessionid", "domain": ".preply.com", "path": "/", "value": "present"}]

    def close(self) -> None:
        self.closed = True
        self.is_open = False


def _open_login_window(missing_session: bool = False) -> _FakeLoginWindow:
    return _FakeLoginWindow(missing_session)


class SavePathTests(unittest.TestCase):
    def test_adds_apkg_and_refuses_empty_name(self) -> None:
        folder = Path("C:/Users/example/Documents")
        self.assertEqual(join_deck_path(folder, "Колода").name, "Колода.apkg")
        self.assertEqual(join_deck_path(folder, "Колода.apkg"), folder / "Колода.apkg")
        self.assertEqual(join_deck_path(folder, "Колода.APKG").name, "Колода.apkg")
        for empty in ("", "   ", ".apkg", " .apkg ", ".", ".."):
            with self.assertRaises(ValueError):
                join_deck_path(folder, empty)
        with self.assertRaises(ValueError):
            join_deck_path(folder, "bad/name")

    def test_save_uses_the_settings_folder_and_save_as_uses_the_picker(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from preply_extractor.app_settings import AppSettings, load_settings, save_settings
        from preply_extractor.gui import MainWindow

        _app()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "settings.json"
            default_dir = root / "default-decks"
            picked_dir = root / "picked-decks"
            newer = root / "newer-default"
            default_dir.mkdir()
            picked_dir.mkdir()
            newer.mkdir()
            save_settings(AppSettings("ru", str(default_dir)), settings)
            built = root / "built.apkg"
            built.write_bytes(b"first-bytes")
            with patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None):
                window = MainWindow(settings_file=settings)
                try:
                    panel = window.save_panel
                    panel.set_result(built, [], 1)
                    panel.name_edit.setText("Deck")
                    window._show_step(window.stack.indexOf(panel))
                    self.assertIn(str(default_dir), panel.default_caption.text())
                    self.assertEqual(Path(panel.folder_edit.text()), default_dir)

                    with patch(
                        "preply_extractor.gui.QFileDialog.getExistingDirectory",
                        side_effect=AssertionError("picker"),
                    ):
                        panel.save_button.click()
                    saved = default_dir / "Deck.apkg"
                    self.assertEqual(saved.read_bytes(), b"first-bytes")
                    self.assertTrue(built.is_file())
                    self.assertTrue(panel.save_button.isEnabled())
                    self.assertTrue(panel.save_as_button.isEnabled())
                    self.assertIn(str(saved), panel.success.text())

                    built.write_bytes(b"replaced-bytes")
                    with (
                        patch(
                            "preply_extractor.gui.QFileDialog.getExistingDirectory",
                            side_effect=AssertionError("picker"),
                        ),
                        patch("preply_extractor.gui.QMessageBox") as box,
                    ):
                        panel.save_button.click()
                        box.assert_not_called()
                    self.assertEqual(saved.read_bytes(), b"replaced-bytes")
                    self.assertTrue(panel.save_button.isEnabled())
                    self.assertTrue(panel.save_as_button.isEnabled())

                    with patch(
                        "preply_extractor.gui.QFileDialog.getExistingDirectory",
                        return_value=str(picked_dir),
                    ) as picker:
                        panel.save_as_button.click()
                    picker.assert_called_once()
                    picked = picked_dir / "Deck.apkg"
                    self.assertEqual(picked.read_bytes(), b"replaced-bytes")
                    self.assertEqual(saved.read_bytes(), b"replaced-bytes")
                    self.assertEqual(Path(panel.folder_edit.text()), default_dir)
                    self.assertTrue(panel.save_button.isEnabled())
                    self.assertTrue(panel.save_as_button.isEnabled())
                    self.assertIn(str(picked), panel.success.text())

                    built.write_bytes(b"third-bytes")
                    with patch(
                        "preply_extractor.gui.QFileDialog.getExistingDirectory",
                        return_value=str(picked_dir),
                    ):
                        with patch("preply_extractor.gui.QMessageBox") as box:
                            box.Icon = QMessageBox.Icon
                            box.StandardButton = QMessageBox.StandardButton
                            box.return_value.button.return_value = None
                            box.return_value.exec.return_value = QMessageBox.StandardButton.No
                            panel.save_as_button.click()
                            self.assertEqual(picked.read_bytes(), b"replaced-bytes")
                            box.return_value.exec.return_value = QMessageBox.StandardButton.Yes
                            panel.save_as_button.click()
                            self.assertEqual(picked.read_bytes(), b"third-bytes")
                    self.assertTrue(panel.save_button.isEnabled())
                    self.assertTrue(panel.save_as_button.isEnabled())

                    dialog = window._make_settings_dialog(load_settings(settings))
                    dialog.folder_edit.setText(str(newer))
                    dialog.cancel_button.click()
                    self.assertIn(str(default_dir), panel.default_caption.text())
                    self.assertNotIn(str(newer), panel.default_caption.text())
                    self.assertEqual(Path(panel.folder_edit.text()), default_dir)

                    dialog = window._make_settings_dialog(load_settings(settings))
                    dialog.folder_edit.setText(str(newer))
                    dialog.ok_button.click()
                    self.assertEqual(Path(panel.folder_edit.text()), newer)
                    self.assertIn(str(newer), panel.default_caption.text())
                    self.assertNotIn(str(default_dir), panel.default_caption.text())

                    with patch(
                        "preply_extractor.gui.QFileDialog.getExistingDirectory",
                        side_effect=AssertionError("picker"),
                    ):
                        panel.save_button.click()
                    self.assertEqual((newer / "Deck.apkg").read_bytes(), b"third-bytes")

                    dialog = window._make_settings_dialog(load_settings(settings))
                    dialog.folder_edit.setText("")
                    dialog.ok_button.click()
                    self.assertIn("не задана", panel.default_caption.text())
                    with (
                        patch(
                            "preply_extractor.gui.QFileDialog.getExistingDirectory",
                            side_effect=AssertionError("picker"),
                        ),
                        patch("preply_extractor.gui.QMessageBox") as box,
                    ):
                        panel.save_button.click()
                        box.assert_not_called()
                    self.assertIn("Сохранить как", panel.success.text())
                    self.assertEqual((newer / "Deck.apkg").read_bytes(), b"third-bytes")
                finally:
                    window.close()


class ChromiumInstallTests(unittest.TestCase):
    def test_skips_install_when_browser_exists(self) -> None:
        calls = []

        def run(command):
            calls.append(command)
            return 0, ""

        started = ensure_playwright_chromium(installed=True, run=run)
        self.assertFalse(started)
        self.assertEqual(calls, [])

    def test_install_command_uses_this_interpreter(self) -> None:
        self.assertEqual(
            install_chromium_command(),
            [sys.executable, "-m", "playwright", "install", "chromium"],
        )
        seen = []

        def run(command):
            seen.append(command)
            return 0, ""

        started = ensure_playwright_chromium(installed=False, run=run)
        self.assertTrue(started)
        self.assertEqual(seen, [[sys.executable, "-m", "playwright", "install", "chromium"]])

    def test_pythonw_install_uses_console_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pythonw = folder / "pythonw.exe"
            python = folder / "python.exe"
            pythonw.write_text("w", encoding="utf-8")
            python.write_text("p", encoding="utf-8")
            command = install_chromium_command(str(pythonw))
        self.assertEqual(command, [str(python), "-m", "playwright", "install", "chromium"])
        self.assertFalse(Path(command[0]).name.lower().startswith("pythonw"))

    def test_missing_package_installs_pip_then_chromium_with_python_exe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pythonw = folder / "pythonw.exe"
            python = folder / "python.exe"
            pythonw.write_text("w", encoding="utf-8")
            python.write_text("p", encoding="utf-8")
            seen: list[list[str]] = []

            def run(command):
                seen.append(list(command))
                return 0, ""

            with patch("preply_extractor.gui_logic.sys.executable", str(pythonw)):
                started = prepare_playwright_runtime(
                    package_installed=False,
                    chromium_installed=False,
                    run=run,
                )
            self.assertEqual(started, seen)
            self.assertEqual(seen[0], install_playwright_package_command(str(pythonw)))
            self.assertEqual(seen[1], install_chromium_command(str(pythonw)))
            self.assertEqual(seen[0][0], str(python))
            self.assertEqual(seen[1][0], str(python))
            self.assertNotIn("pythonw", Path(seen[0][0]).name.lower())

    def test_module_error_stays_visible(self) -> None:
        detail = (
            r"C:\Users\celis\AppData\Local\Programs\Python\Python313\python.exe: "
            "No module named playwright"
        )
        text = missing_browser_message(detail)
        self.assertIn("No module named playwright", text)
        self.assertIn(r"Python313\python.exe", text)
        self.assertNotIn("консол", text.lower())
        self.assertNotIn("выполните", text.lower())

    def test_nonzero_exit_is_failure(self) -> None:
        def run(_command):
            return 1, "download failed"

        with self.assertRaises(BrowserInstallError) as caught:
            ensure_playwright_chromium(installed=False, run=run)
        self.assertIn("download failed", str(caught.exception))
        self.assertEqual(caught.exception.returncode, 1)

    def test_subprocess_exit_codes(self) -> None:
        command = [sys.executable, "-m", "playwright", "install", "chromium"]
        with patch("preply_extractor.gui_logic.subprocess.run") as mocked:
            mocked.return_value.returncode = 0
            mocked.return_value.stderr = ""
            mocked.return_value.stdout = ""
            self.assertTrue(ensure_playwright_chromium(installed=False))
            mocked.assert_called_once_with(command, capture_output=True, text=True, check=False)
        with patch("preply_extractor.gui_logic.subprocess.run") as mocked:
            mocked.return_value.returncode = 2
            mocked.return_value.stderr = "network down"
            mocked.return_value.stdout = ""
            with self.assertRaises(BrowserInstallError) as caught:
                ensure_playwright_chromium(installed=False)
            self.assertEqual(caught.exception.returncode, 2)
            self.assertIn("network down", str(caught.exception))
        with patch("preply_extractor.gui_logic.subprocess.run") as mocked:
            ensure_playwright_chromium(installed=True)
            mocked.assert_not_called()


class WordListWidgetTests(unittest.TestCase):
    def test_select_all_clear_and_confirm(self) -> None:
        _app()
        panel = WordListPanel()
        panel.set_words(_words(), keep_checks=False)
        self.assertFalse(panel.next_button.isEnabled())
        self.assertEqual(panel.selected_words(), [])

        panel.select_all_button.click()
        self.assertTrue(panel.next_button.isEnabled())
        self.assertEqual([word.lexeme for word in panel.selected_words()], ["speak", "learn"])

        panel.clear_button.click()
        self.assertFalse(panel.next_button.isEnabled())
        self.assertEqual(panel.selected_words(), [])
        panel.close()

    def test_refresh_keeps_checks_for_words_that_remain(self) -> None:
        _app()
        panel = WordListPanel()
        words = _words()
        panel.set_words(words, keep_checks=False)
        panel.select_all()
        panel.set_words(
            [
                words[1],
                VocabWord(id="new", lexeme="new", definition="recently made", examples=[]),
            ],
            keep_checks=True,
        )
        self.assertEqual([word.lexeme for word in panel.selected_words()], ["learn"])
        self.assertTrue(panel.next_button.isEnabled())
        panel.close()


def _subject_aliases_sent(subject: str) -> list[str]:
    """Post vocabulary queries the way the export worker does, without a browser."""
    sent: list[str] = []

    def post_json(path: str, payload: dict) -> tuple[int, str]:
        del path
        sent.append(str(payload["variables"]["subjectAlias"]))
        body = {
            "data": {
                "vocabularyBank": {
                    "summary": {
                        "pendingCount": 0,
                        "learningReadyToPracticeCount": 0,
                        "learningUpNextCount": 0,
                        "masteredCount": 0,
                    },
                    "currentWords": {"hasNext": False, "endCursor": None, "nodes": []},
                }
            }
        }
        return 200, json.dumps(body)

    fetch_vocabulary(
        post_json,
        subject=subject,
        language_code=UI_LANGUAGE,
        page_size=PAGE_SIZE,
        delay_seconds=0,
    )
    return sent


class SubjectNormalizationTests(unittest.TestCase):
    def test_load_lowercases_alias_and_does_not_invent_english(self) -> None:
        worker = ExportWorker()
        worker.load("  Spanish  ")
        self.assertEqual(worker._commands.get_nowait(), ("load", "spanish"))
        worker.load("   ")
        self.assertEqual(worker._commands.get_nowait(), ("load", ""))
        worker.use_browser_cookies()
        self.assertEqual(worker._commands.get_nowait()[0], "cookies")
        self.assertTrue(worker._commands.empty())

    def test_chosen_alias_is_sent_as_subject_alias(self) -> None:
        subject = vocabulary_alias(["English", "Spanish"], "  Spanish ")
        self.assertEqual(subject, "spanish")
        payload = _payload(CATEGORIES[0][0], PAGE_SIZE, None, UI_LANGUAGE, subject or "")
        self.assertEqual(payload["variables"]["subjectAlias"], "spanish")
        aliases = _subject_aliases_sent(subject or "")
        self.assertTrue(aliases)
        self.assertEqual(set(aliases), {"spanish"})

    def test_blank_subject_is_not_fetched_as_english(self) -> None:
        _app()
        worker = ExportWorker()
        failed: list[str] = []
        worker.load_failed.connect(failed.append)
        worker._load("")
        self.assertEqual(failed, ["Язык не выбран. Список слов не загружен."])


class ProfileAndLanguageTests(unittest.TestCase):
    def test_profile_shows_id_and_returned_name(self) -> None:
        profile = parse_learner_profile(
            {
                "data": {
                    "currentUser": {
                        "id": 42,
                        "firstName": "Ada",
                        "fullName": "Ada Lovelace",
                        "client": {
                            "id": 9,
                            "subjects": [
                                {"subject": {"id": 1, "alias": "English"}},
                                {"subject": {"id": 2, "alias": "spanish"}},
                            ],
                        },
                    }
                }
            }
        )
        self.assertEqual(profile.user_id, "42")
        self.assertEqual(profile.display_name, "Ada Lovelace")
        self.assertEqual(format_profile(profile.user_id, profile.display_name), "Идентификатор: 42\nИмя: Ada Lovelace")
        self.assertEqual(profile.subject_aliases, ("english", "spanish"))

    def test_missing_name_is_not_invented(self) -> None:
        profile = parse_learner_profile(
            {"data": {"currentUser": {"id": "7", "firstName": "   ", "fullName": None, "client": None}}}
        )
        self.assertEqual(profile.display_name, None)
        self.assertEqual(format_profile(profile.user_id, profile.display_name), "Идентификатор: 7\nИмя: нет в ответе")
        self.assertIsNone(vocabulary_alias(list(profile.subject_aliases)))

    def test_first_name_is_used_when_full_name_is_absent(self) -> None:
        profile = parse_learner_profile(
            {
                "data": {
                    "currentUser": {
                        "id": "7",
                        "firstName": "Ada",
                        "client": {"subjects": [{"subject": {"alias": "spanish"}}]},
                    }
                }
            }
        )
        self.assertEqual(profile.display_name, "Ada")
        self.assertEqual(format_profile(profile.user_id, profile.display_name), "Идентификатор: 7\nИмя: Ada")

    def test_zero_languages_do_not_request_vocabulary(self) -> None:
        self.assertIsNone(vocabulary_alias([]))
        self.assertIsNone(vocabulary_alias(["  "]))

    def test_one_language_is_selected_without_a_chooser(self) -> None:
        _app()
        panel = WordListPanel()
        try:
            self.assertFalse(hasattr(panel, "subject_edit"))
            subject = vocabulary_alias(["  Spanish "])
            self.assertEqual(subject, "spanish")
            panel.set_language("Spanish", subject or "", False)
            self.assertEqual(panel.selected_subject(), "spanish")
            self.assertEqual(panel.language_label.text(), "Spanish")
            self.assertTrue(panel.switch_button.isHidden())
            aliases = _subject_aliases_sent(subject or "")
            self.assertEqual(set(aliases), {"spanish"})
        finally:
            panel.close()

    def test_many_languages_wait_for_confirmation(self) -> None:
        _app()
        panel = LanguagePanel()
        words = WordListPanel()
        try:
            aliases = ["english", "spanish"]
            self.assertIsNone(vocabulary_alias(aliases))
            panel.set_languages([(alias, alias.capitalize()) for alias in aliases])
            self.assertFalse(panel.confirm_button.isEnabled())
            panel.language_list.setCurrentRow(1)
            self.assertTrue(panel.confirm_button.isEnabled())
            self.assertEqual(panel.selected_alias(), "spanish")
            chosen = vocabulary_alias(aliases, panel.selected_alias())
            self.assertEqual(chosen, "spanish")
            words.set_language("Spanish", chosen or "", True)
            self.assertFalse(words.switch_button.isHidden())
            self.assertEqual(set(_subject_aliases_sent(chosen or "")), {"spanish"})
        finally:
            panel.close()
            words.close()

    def test_profile_without_a_user_is_an_error(self) -> None:
        with self.assertRaises(PreplyError):
            parse_learner_profile({"data": {"currentUser": None}})


class BrowserRetryTests(unittest.TestCase):
    def test_missing_browser_text_has_no_terminal_command(self) -> None:
        samples = (
            missing_browser_message("Chromium for Playwright is not installed. Run: playwright install chromium"),
            missing_browser_message("Please run the following command:\nplaywright install chromium"),
            russian_error(BrowserInstallError(1, "playwright install chromium")),
            russian_error(PreplyError("Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium")),
            russian_error(PreplyError("Chromium for Playwright is not installed. Run: playwright install chromium")),
        )
        for text in samples:
            lowered = text.lower()
            self.assertNotIn("playwright install", lowered)
            self.assertNotIn("консол", lowered)
            self.assertNotIn("выполните", lowered)
            self.assertNotIn("console", lowered)
            self.assertIn("Повторить", text)

    def test_authorize_button_invokes_its_slot(self) -> None:
        _app()
        panel = LoginPanel()
        calls: list[str] = []
        panel.authorize_button.clicked.connect(lambda _checked=False: calls.append("authorize"))
        panel.authorize_button.click()
        self.assertEqual(calls, ["authorize"])
        self.assertTrue(panel.authorize_button.isEnabled())
        panel.close()

    def test_cookie_sign_in_does_not_launch_playwright(self) -> None:
        _app()
        worker = ExportWorker()
        ready: list[object] = []
        worker.profile_ready.connect(ready.append)

        login = _open_login_window()
        worker._login = login

        def session_from_cookies(cookies, _language):
            self.assertEqual([item["name"] for item in cookies], ["sessionid"])
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"
            session.post_json = lambda *_a, **_k: (200, "{}")
            session.close = lambda: None
            return session

        with (
            patch("preply_extractor.gui.subprocess.run") as run,
            patch("preply_extractor.browser_login.load_default_browser_preply_cookies", side_effect=AssertionError("opera cookies")) as loaded,
            patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera cookies")),
            patch("os.startfile", side_effect=AssertionError("tab")),
            patch("preply_extractor.gui.BrowserCookieSession", side_effect=session_from_cookies),
            patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
            patch("preply_extractor.gui.fetch_learner_profile", return_value=object()),
            patch("preply_extractor.gui.fetch_vocabulary") as vocab,
        ):
            worker.use_browser_cookies()
            self.assertEqual(worker._commands.get_nowait()[0], "cookies")
            worker._connect()
        run.assert_not_called()
        loaded.assert_not_called()
        launched.assert_not_called()
        self.assertEqual(len(ready), 1)
        self.assertTrue(login.closed)
        self.assertIsNone(worker._login)
        vocab.assert_not_called()

    def test_retry_skips_install_when_browser_exists_and_logs_in(self) -> None:
        _app()
        worker = ExportWorker()
        order: list[str] = []

        def session_from_cookies(_cookies, _language):
            order.append("login")
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"
            session.post_json = lambda *_a, **_k: (200, "{}")
            session.close = lambda: None
            return session

        def load_profile(*_args, **_kwargs):
            order.append("profile")
            return object()

        worker._login = _open_login_window()
        with (
            patch("preply_extractor.gui.subprocess.run") as run,
            patch("preply_extractor.browser_login.load_default_browser_preply_cookies", side_effect=AssertionError("opera cookies")),
            patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera cookies")),
            patch("os.startfile", side_effect=AssertionError("tab")),
            patch("preply_extractor.gui.BrowserCookieSession", side_effect=session_from_cookies),
            patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
            patch("preply_extractor.gui.fetch_learner_profile", side_effect=load_profile),
            patch("preply_extractor.gui.fetch_vocabulary") as vocab,
        ):
            worker._close_session()
            worker._connect()
        run.assert_not_called()
        launched.assert_not_called()
        self.assertEqual(order, ["login", "profile"])
        vocab.assert_not_called()

    def test_failed_install_can_be_retried(self) -> None:
        _app()
        worker = ExportWorker()
        failed: list[str] = []
        worker.dependency_failed.connect(failed.append)

        def run_install(*_args, **_kwargs):
            completed = type("Completed", (), {})()
            completed.returncode = 1
            completed.stderr = "network down\nplaywright install chromium"
            completed.stdout = ""
            return completed

        with (
            patch("preply_extractor.gui_logic.module_importable", return_value=True),
            patch("preply_extractor.gui_logic.playwright_importable", return_value=True),
            patch("preply_extractor.gui_logic.chromium_executable_exists", return_value=False),
            patch("preply_extractor.gui.subprocess.run", side_effect=run_install),
        ):
            worker._install_dependencies()
        self.assertEqual(len(failed), 1)
        self.assertNotIn("playwright install", failed[0].lower())
        self.assertIn("Исправить", failed[0])
        self.assertIn("network down", failed[0])
        worker.install_dependencies()
        self.assertEqual(worker._commands.get_nowait()[0], "install")

    def test_retry_under_pythonw_uses_python_exe_and_shows_stderr(self) -> None:
        _app()
        worker = ExportWorker()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pythonw = folder / "pythonw.exe"
            python = folder / "python.exe"
            pythonw.write_text("w", encoding="utf-8")
            python.write_text("p", encoding="utf-8")

            def run_install(*_args, **_kwargs):
                completed = type("Completed", (), {})()
                completed.returncode = 1
                completed.stderr = "download failed: disk full"
                completed.stdout = "playwright install chromium"
                return completed

            failed: list[str] = []
            worker.dependency_failed.connect(failed.append)
            with (
                patch("preply_extractor.gui_logic.sys.executable", str(pythonw)),
                patch("preply_extractor.gui_logic.module_importable", return_value=True),
                patch("preply_extractor.gui_logic.playwright_importable", return_value=False),
                patch("preply_extractor.gui.subprocess.run", side_effect=run_install) as run,
                patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
                patch("preply_extractor.gui.fetch_vocabulary") as vocab,
            ):
                worker._install_dependencies()
            self.assertEqual(run.call_args.args[0][0], str(python))
            self.assertFalse(Path(run.call_args.args[0][0]).name.lower().startswith("pythonw"))
            self.assertEqual(len(failed), 1)
            self.assertIn("disk full", failed[0])
            self.assertNotIn("playwright install", failed[0].lower())
            self.assertNotIn("консол", failed[0].lower())
            launched.assert_not_called()
            vocab.assert_not_called()


_READY = {"pyside6": True, "requests": True, "playwright": True, "chromium": True}


class StartupFlowTests(unittest.TestCase):
    def test_install_commands_cover_only_what_is_missing(self) -> None:
        missing_package = install_commands_for(
            {"pyside6": True, "requests": True, "playwright": False, "chromium": False}
        )
        self.assertEqual(len(missing_package), 2)
        self.assertEqual(missing_package[0][1:4], ["-m", "pip", "install"])
        self.assertEqual(missing_package[0][-1], "playwright==1.49.1")
        self.assertEqual(missing_package[1][-3:], ["playwright", "install", "chromium"])
        self.assertFalse(Path(missing_package[0][0]).name.lower().startswith("pythonw"))
        only_browser = install_commands_for({**_READY, "chromium": False})
        self.assertEqual(len(only_browser), 1)
        self.assertEqual(only_browser[0][-3:], ["playwright", "install", "chromium"])
        self.assertEqual(install_commands_for(_READY), [])

    def test_start_again_returns_to_the_first_step_with_an_empty_word_list(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            saved = Path(tmp) / "kept.apkg"
            saved.write_bytes(b"saved-deck")
            with (
                patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None),
                patch("os.startfile") as opened,
                patch("preply_extractor.gui.SeparateProfileWindow.open") as launched,
            ):
                window = MainWindow(settings_file=path)
                try:
                    window.worker._commands.get_nowait()
                    window.words_panel.set_words(_words(), keep_checks=False)
                    window.words_panel.select_all()
                    self.assertGreater(window.words_panel.word_list.count(), 0)
                    self.assertGreater(len(window.words_panel.selected_words()), 0)
                    window._temp_dir = Path(tmp) / "temp-deck"
                    window._temp_dir.mkdir()
                    window._show_step(_STEP_SAVE)
                    window.save_panel._saved = True
                    window.save_panel._saved_path = saved
                    window.save_panel.restart_button.setEnabled(True)
                    window.save_panel.restart_button.show()
                    self.assertEqual(window.save_panel.restart_button.text(), "Начать заново")
                    window.save_panel.restart_button.click()
                    self.assertEqual(window.stack.currentIndex(), _STEP_DEPS)
                    self.assertEqual(window.words_panel.word_list.count(), 0)
                    self.assertEqual(window.words_panel.selected_words(), [])
                    self.assertFalse(window.save_panel.saved())
                    self.assertTrue(saved.is_file())
                    self.assertFalse(window._temp_dir.exists() if window._temp_dir else False)
                    self.assertEqual(window.worker._commands.get_nowait()[0], "release")
                    self.assertEqual(window.worker._commands.get_nowait()[0], "check")
                    opened.assert_not_called()
                    launched.assert_not_called()
                    window._on_dependencies(dict(_READY))
                    self.assertEqual(window.stack.currentIndex(), _STEP_LOGIN)
                    opened.assert_not_called()
                    launched.assert_not_called()
                finally:
                    window.close()

    def test_startup_check_does_not_open_a_browser_until_authorize(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with (
                patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None),
                patch("os.startfile") as opened,
                patch("preply_extractor.gui.SeparateProfileWindow.open") as launched,
            ):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    self.assertEqual(window.stack.currentIndex(), _STEP_DEPS)
                    self.assertEqual(window.worker._commands.get_nowait()[0], "check")
                    self.assertFalse(hasattr(window, "theme_combo"))
                    launched.assert_not_called()
                    opened.assert_not_called()
                    from preply_extractor.ui_text import tr

                    hint = window.login_panel.hint.text().lower()
                    english = tr("login_hint", language="en").lower()
                    self.assertNotIn("новой вкладкой", hint)
                    self.assertNotIn("new tab", english)
                    self.assertNotIn("default browser", english)
                    self.assertIn("google", hint)
                    self.assertIn("google", english)
                    window.login_panel.authorize_button.click()
                    self.assertEqual(window.worker._commands.get_nowait()[0], "open-login")
                    self.assertTrue(window.login_panel.continue_button.isHidden())
                    opened.assert_not_called()
                    launched.assert_not_called()
                    window._on_login_window_opened()
                    self.assertFalse(window.login_panel.continue_button.isHidden())
                    window.login_panel.continue_button.click()
                    self.assertEqual(window.worker._commands.get_nowait()[0], "cookies")
                    launched.assert_not_called()
                    opened.assert_not_called()
                finally:
                    window.close()

    def test_ready_dependencies_advance_without_opening_a_browser(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with (
                patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None),
                patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
            ):
                window = MainWindow(settings_file=path)
                try:
                    window._on_dependencies(dict(_READY))
                    self.assertEqual(window.stack.currentIndex(), _STEP_LOGIN)
                    self.assertIn("Авторизоваться", window.login_panel.authorize_button.text())
                    self.assertFalse(window.deps_panel.install_button.isVisible())
                    launched.assert_not_called()
                    self.assertEqual(window.worker._commands.get_nowait()[0], "check")
                finally:
                    window.close()

    def test_missing_playwright_shows_install_and_does_not_open_a_browser(self) -> None:
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with (
                patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None),
                patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
            ):
                window = MainWindow(settings_file=path)
                try:
                    window._on_dependencies({**_READY, "playwright": False, "chromium": False})
                    self.assertEqual(window.stack.currentIndex(), _STEP_DEPS)
                    self.assertTrue(window.deps_panel.install_button.isEnabled())
                    self.assertEqual(window.deps_panel.install_button.text(), "Установить")
                    self.assertEqual(window.deps_panel.rows["playwright"].name.text(), "Playwright")
                    self.assertEqual(window.deps_panel.rows["playwright"]._state, "failed")
                    self.assertEqual(window.deps_panel.rows["playwright"].mark.text(), "✕")
                    self.assertEqual(window.deps_panel.rows["chromium"]._state, "failed")
                    self.assertEqual(window.deps_panel.rows["pyside6"]._state, "ok")
                    self.assertEqual(window.deps_panel.rows["pyside6"].mark.text(), "✓")
                    launched.assert_not_called()
                    window.worker._commands.get_nowait()
                    window.deps_panel.install_button.click()
                    self.assertEqual(window.worker._commands.get_nowait()[0], "install")
                    launched.assert_not_called()
                finally:
                    window.close()


class SeparateWindowTests(unittest.TestCase):
    def test_authorize_starts_normal_chrome_without_automation_flags(self) -> None:
        _app()
        from preply_extractor.browser_login import PREPLY_AUTH_URL, BrowserCookieError, SeparateProfileWindow

        class Proc:
            def __init__(self) -> None:
                self.alive = True

            def poll(self) -> int | None:
                return None if self.alive else 0

            def terminate(self) -> None:
                self.alive = False

            def wait(self, timeout: float | None = None) -> int:
                return 0

        commands: list[list[str]] = []

        def popen(command, **kwargs):
            self.assertNotIn("shell", kwargs)
            commands.append(list(command))
            return Proc()

        chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        worker = ExportWorker()
        opened: list[bool] = []
        worker.login_window_opened.connect(lambda: opened.append(True))
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / ".preply-profile"
            with (
                patch("os.startfile", side_effect=AssertionError("tab")),
                patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("playwright launch")),
                patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera")),
                patch("preply_extractor.browser_login.debug_port_is_open", return_value=False),
                patch("preply_extractor.browser_login.installed_chrome_path", return_value=chrome),
                patch("preply_extractor.browser_login.subprocess.Popen", side_effect=popen),
                patch("preply_extractor.gui.program_profile_directory", return_value=profile),
            ):
                worker._open_login()
                worker._open_login()
                self.assertEqual(len(commands), 1)
                worker._login._process.alive = False
                worker._open_login()
            command = commands[0]
            joined = " ".join(command).lower()
            directory = next(part.split("=", 1)[1] for part in command if part.startswith("--user-data-dir="))
            self.assertTrue(str(directory).endswith(".preply-profile"))
            self.assertIn("--remote-debugging-port=9333", command)
            self.assertNotIn("--enable-automation", joined)
            self.assertNotIn("--headless", joined)
            self.assertNotIn("automationcontrolled", joined)
            self.assertNotIn("opera software", joined)
            self.assertNotIn("\\user data", directory.lower())
            self.assertEqual(command[-1], PREPLY_AUTH_URL)
            self.assertEqual(command[0], str(chrome))
            self.assertEqual(len(commands), 2)
            self.assertEqual(opened, [True, True, True])
            self.assertTrue(profile.is_dir())
        appdata = os.environ.get("APPDATA", "")
        with self.assertRaises(BrowserCookieError):
            SeparateProfileWindow(Path(appdata) / "Opera Software" / "Opera Stable")
        local = os.environ.get("LOCALAPPDATA", "")
        with self.assertRaises(BrowserCookieError):
            SeparateProfileWindow(Path(local) / "Google" / "Chrome" / "User Data")

    def test_signed_in_reads_the_debug_port_not_a_cookie_file(self) -> None:
        _app()
        import hashlib

        from preply_extractor.browser_login import SeparateProfileWindow

        class Proc:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout: float | None = None) -> int:
                return 0

        seen: dict[str, object] = {}

        def cookies(port: int) -> list[dict]:
            seen["port"] = port
            seen["endpoint"] = f"http://127.0.0.1:{port}/json/version"
            host = ".preply.com"
            raw = hashlib.sha256(host.encode("utf-8")).digest() + b"ok"
            return [
                {"name": "sessionid", "value": raw.decode("latin-1"), "domain": host, "path": "/"},
                {"name": "sid", "value": "other", "domain": ".google.com", "path": "/"},
            ]

        def session_from_cookies(cookie_list, _language):
            self.assertEqual([item["name"] for item in cookie_list], ["sessionid"])
            self.assertEqual(cookie_list[0]["value"], "ok")
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"
            session.post_json = lambda *_args, **_kwargs: (200, "{}")
            session.close = lambda: None
            return session

        with tempfile.TemporaryDirectory() as tmp:
            window = SeparateProfileWindow(Path(tmp) / ".preply-profile")
            process = Proc()
            window._process = process
            worker = ExportWorker()
            ready: list[object] = []
            worker.profile_ready.connect(ready.append)
            worker._login = window
            with (
                patch("os.startfile", side_effect=AssertionError("tab")),
                patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("playwright launch")),
                patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera")),
                patch("preply_extractor.browser_login._copy_bytes_shared", side_effect=AssertionError("opera")),
                patch("preply_extractor.browser_login.cdp_network_cookies", side_effect=cookies),
                patch("preply_extractor.gui.BrowserCookieSession", side_effect=session_from_cookies),
                patch("preply_extractor.gui.fetch_learner_profile", return_value=object()),
            ):
                worker._connect()
            self.assertEqual(seen["port"], 9333)
            self.assertEqual(seen["endpoint"], "http://127.0.0.1:9333/json/version")
            self.assertEqual(len(ready), 1)
            self.assertIsNone(window._process)
            self.assertTrue(process.terminated)

    def test_closed_debug_port_shows_the_closed_window_message(self) -> None:
        _app()
        from preply_extractor.browser_login import SeparateProfileWindow

        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        with tempfile.TemporaryDirectory() as tmp:
            worker._login = SeparateProfileWindow(Path(tmp) / ".preply-profile")
            with (
                patch("preply_extractor.browser_login.debug_port_is_open", return_value=False),
                patch("preply_extractor.browser_login.cdp_network_cookies", side_effect=AssertionError("cdp")),
                patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera")),
                patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("playwright launch")),
            ):
                worker._connect()
        self.assertEqual(len(failed), 1)
        self.assertIn("Окно входа закрыто", failed[0])
        self.assertIn("Авторизоваться", failed[0])
        self.assertNotIn("traceback", failed[0].lower())

    def test_missing_chrome_is_reported_without_playwright(self) -> None:
        _app()
        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("preply_extractor.gui.program_profile_directory", return_value=Path(tmp) / ".preply-profile"),
                patch("preply_extractor.browser_login.debug_port_is_open", return_value=False),
                patch("preply_extractor.browser_login.installed_chrome_path", return_value=None),
                patch("preply_extractor.browser_login.subprocess.Popen", side_effect=AssertionError("chrome")),
                patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("playwright launch")),
            ):
                worker._open_login()
        self.assertEqual(len(failed), 1)
        self.assertIn("Google Chrome не найден", failed[0])
        self.assertNotIn("chromium", failed[0].lower())

    def test_missing_session_leaves_the_chrome_window_open(self) -> None:
        _app()
        from preply_extractor.browser_login import SeparateProfileWindow

        class Proc:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.terminated = True

        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        with tempfile.TemporaryDirectory() as tmp:
            window = SeparateProfileWindow(Path(tmp) / ".preply-profile")
            process = Proc()
            window._process = process
            worker._login = window

            def cookies(_port: int) -> list[dict]:
                return [{"name": "csrftoken", "value": "token", "domain": ".preply.com", "path": "/"}]

            with (
                patch("preply_extractor.browser_login.cdp_network_cookies", side_effect=cookies),
                patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera")),
                patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("playwright launch")),
            ):
                worker._connect()
        self.assertIn("Я вошёл", failed[0])
        self.assertFalse(process.terminated)
        self.assertIs(worker._login, window)

    def test_cdp_client_uses_the_localhost_debug_socket(self) -> None:
        import socket
        import threading

        from preply_extractor.browser_login import cdp_network_cookies

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen(2)
        done = threading.Event()

        def _discard_client_frame(conn: socket.socket, pending: bytes) -> bytes:
            buffer = bytearray(pending)
            while len(buffer) < 2:
                buffer.extend(conn.recv(4096))
            length = buffer[1] & 0x7F
            offset = 2
            if length == 126:
                while len(buffer) < 4:
                    buffer.extend(conn.recv(4096))
                length = int.from_bytes(buffer[2:4], "big")
                offset = 4
            if buffer[1] & 0x80:
                offset += 4
            while len(buffer) < offset + length:
                buffer.extend(conn.recv(4096))
            return bytes(buffer[offset + length :])

        def handle(conn: socket.socket) -> None:
            try:
                data = bytearray()
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data.extend(chunk)
                if data.startswith(b"GET /json/list"):
                    body = json.dumps(
                        [
                            {
                                "type": "page",
                                "url": "https://preply.com/en/practice/vocab-bank",
                                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/test",
                            }
                        ]
                    ).encode("utf-8")
                    head = (
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(body)).encode("ascii")
                        + b"\r\nConnection: close\r\n\r\n"
                    )
                    conn.sendall(head + body)
                    return
                conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
                rest = data.split(b"\r\n\r\n", 1)[1]
                replies = [
                    {"id": 1, "result": {}},
                    {
                        "id": 2,
                        "result": {
                            "cookies": [
                                {"name": "sessionid", "value": "ok", "domain": ".preply.com", "path": "/"}
                            ]
                        },
                    },
                ]
                for reply in replies:
                    rest = _discard_client_frame(conn, rest)
                    payload = json.dumps(reply).encode("utf-8")
                    conn.sendall(bytes([0x81, len(payload)]) + payload)
            finally:
                conn.close()
                done.set()

        def accept_loop() -> None:
            for _ in range(2):
                conn, _addr = server.accept()
                threading.Thread(target=handle, args=(conn,), daemon=True).start()

        threading.Thread(target=accept_loop, daemon=True).start()
        try:
            cookies = cdp_network_cookies(port)
        finally:
            server.close()
        self.assertEqual([item["name"] for item in cookies], ["sessionid"])
        self.assertEqual(cookies[0]["domain"], ".preply.com")
        done.wait(2)

    def test_preply_session_cookie_is_accepted_for_either_domain(self) -> None:
        from preply_extractor.browser_login import cookies_from_browser_api
        from preply_extractor.ui_text import tr

        for domain in (".preply.com", "preply.com", "www.preply.com"):
            cookies = cookies_from_browser_api(
                [{"name": "sessionid", "value": "a" * 32, "domain": domain, "path": "/"}]
            )
            self.assertEqual([item["name"] for item in cookies], ["sessionid"])
            self.assertEqual(cookies[0]["value"], "a" * 32)
            self.assertTrue(str(cookies[0]["domain"]).endswith("preply.com"))
        russian = tr("session_inactive", language="ru")
        english = tr("session_inactive", language="en")
        missing = tr("login_not_finished", language="ru")
        self.assertIn("Я вошёл", russian)
        self.assertNotIn("Обновить", russian)
        self.assertIn("I've signed in", english)
        self.assertNotIn("Refresh", english)
        self.assertIn("Я вошёл", missing)
        self.assertNotIn("Обновить", missing)

    def test_closed_context_during_sign_in_shows_a_short_message(self) -> None:
        _app()

        class TargetClosedError(Exception):
            pass

        class ClosedWindow:
            is_open = True

            def __init__(self) -> None:
                self.closed = False

            def read_preply_cookies(self) -> list[dict]:
                raise TargetClosedError("browser-context page context for browser has been closed")

            def close(self) -> None:
                self.closed = True

        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        login = ClosedWindow()
        worker._login = login
        with (
            patch("os.startfile", side_effect=AssertionError("tab")),
            patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera")),
            patch("preply_extractor.browser_login._copy_bytes_shared", side_effect=AssertionError("opera")),
        ):
            worker._connect()
        self.assertEqual(len(failed), 1)
        self.assertIn("Окно входа закрыто", failed[0])
        self.assertIn("Авторизоваться", failed[0])
        self.assertNotIn("has been closed", failed[0].lower())
        self.assertNotIn("traceback", failed[0].lower())
        self.assertTrue(login.closed)
        self.assertIsNone(worker._login)

        worker = ExportWorker()
        failed.clear()
        worker.connect_failed.connect(failed.append)
        login = _open_login_window()
        worker._login = login

        def session_from_cookies(_cookies, _language):
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"

            def post_json(*_args, **_kwargs):
                raise TargetClosedError("has been closed")

            session.post_json = post_json
            session.close = lambda: None
            return session

        with (
            patch("os.startfile", side_effect=AssertionError("tab")),
            patch("preply_extractor.gui.BrowserCookieSession", side_effect=session_from_cookies),
        ):
            worker._connect()
        self.assertEqual(len(failed), 1)
        self.assertIn("Окно входа закрыто", failed[0])
        self.assertNotIn("has been closed", failed[0].lower())
        self.assertTrue(login.closed)
        self.assertIsNone(worker._login)

    def test_profile_failure_leaves_the_sign_in_window_open(self) -> None:
        _app()
        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        login = _open_login_window()
        worker._login = login

        def session_from_cookies(_cookies, _language):
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"
            session.post_json = lambda *_a, **_k: (200, "{}")
            session.close = lambda: None
            return session

        with (
            patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera")),
            patch("os.startfile", side_effect=AssertionError("tab")),
            patch("preply_extractor.gui.BrowserCookieSession", side_effect=session_from_cookies),
            patch("preply_extractor.gui.fetch_learner_profile", side_effect=RuntimeError("profile")),
        ):
            worker._connect()
        self.assertFalse(login.closed)
        self.assertIs(worker._login, login)
        self.assertEqual(len(failed), 1)


class DefaultBrowserTests(unittest.TestCase):
    def test_progid_selects_the_cookie_store(self) -> None:
        from preply_extractor.browser_login import browser_family

        self.assertEqual(browser_family("FirefoxURL-308046B0AF4A39CB"), "firefox")
        self.assertEqual(browser_family("ChromeHTML"), "chrome")
        self.assertEqual(browser_family("MSEdgeHTM"), "edge")
        self.assertEqual(browser_family("OperaStable"), "opera")
        self.assertEqual(browser_family("OperaGXStable"), "opera-gx")

    def test_opera_stable_uses_its_own_cookie_database(self) -> None:
        from preply_extractor.browser_login import (
            BrowserCookieError,
            chromium_cookie_database,
            chromium_user_data_dir,
            load_default_browser_preply_cookies,
        )

        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp)
            root = appdata / "Opera Software" / "Opera Stable"
            (root / "Network").mkdir(parents=True)
            database = root / "Network" / "Cookies"
            database.write_bytes(b"")
            (root / "Local State").write_text("{}", encoding="utf-8")
            seen: dict[str, object] = {}

            def fake_read(path, key):
                seen["database"] = path
                seen["key"] = key
                return [{"domain": ".preply.com", "name": "sessionid", "value": "present", "path": "/"}]

            def fake_key(user_data):
                seen["user_data"] = user_data
                return b"key"

            legacy_root = appdata / "Opera Software" / "legacy"
            legacy_root.mkdir(parents=True)
            legacy = legacy_root / "Cookies"
            legacy.write_bytes(b"")
            gx = appdata / "Opera Software" / "Opera GX Stable"
            gx.mkdir()

            with (
                patch.dict(os.environ, {"APPDATA": str(appdata)}),
                patch("preply_extractor.browser_login.read_chromium_cookies", fake_read),
                patch("preply_extractor.browser_login.chromium_master_key", fake_key),
                patch("preply_extractor.browser_login._copy_sqlite", side_effect=AssertionError("cookie copy")),
                patch(
                    "preply_extractor.preply_api.BrowserSession.__enter__",
                    side_effect=AssertionError("playwright"),
                ),
            ):
                try:
                    cookies = load_default_browser_preply_cookies("OperaStable")
                except BrowserCookieError as exc:
                    self.fail(str(exc))
                self.assertEqual(chromium_user_data_dir("opera"), root)
                self.assertEqual(chromium_user_data_dir("opera-gx"), gx)
            self.assertEqual(seen["database"], database)
            self.assertEqual(seen["user_data"], root)
            self.assertEqual(seen["key"], b"key")
            self.assertNotIn(".preply-profile", str(seen["database"]).lower())
            self.assertEqual(chromium_cookie_database(legacy_root, "opera"), legacy)
            self.assertEqual([cookie["name"] for cookie in cookies], ["sessionid"])
            self.assertTrue(all(str(cookie["domain"]).endswith("preply.com") for cookie in cookies))
            self.assertFalse(any("google" in str(cookie["domain"]) for cookie in cookies))

    def test_chrome_cookie_database_stays_in_the_profile(self) -> None:
        from preply_extractor.browser_login import chromium_cookie_database, chromium_user_data_dir

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            root = local / "Google" / "Chrome" / "User Data"
            profile = root / "Default" / "Network"
            profile.mkdir(parents=True)
            database = profile / "Cookies"
            database.write_bytes(b"")
            (root / "Local State").write_text('{"profile": {"last_used": "Default"}}', encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                self.assertEqual(chromium_user_data_dir("chrome"), root)
                self.assertEqual(chromium_cookie_database(root, "chrome"), database)

    def test_locked_cookie_file_is_copied_with_shared_read(self) -> None:
        import ctypes
        import shutil
        from ctypes import wintypes

        from preply_extractor import browser_login

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Cookies"
            payloads = {
                str(source): b"db-bytes",
                str(source) + "-wal": b"wal-bytes",
                str(source) + "-shm": b"shm-bytes",
                str(source) + "-journal": b"journal-bytes",
            }
            for name, body in payloads.items():
                Path(name).write_bytes(body)

            class SharedKernel:
                def __init__(self) -> None:
                    self.calls: list[dict[str, object]] = []
                    self._open: dict[int, bytes] = {}
                    self._seq = 0

                def CreateFileW(self, name, access, share, security, disposition, flags, template):
                    self.calls.append(
                        {
                            "name": str(name),
                            "access": int(access),
                            "share": int(share),
                            "disposition": int(disposition),
                        }
                    )
                    key = str(name)
                    if key not in payloads:
                        ctypes.set_last_error(2)
                        return ctypes.c_void_p(-1).value
                    self._seq += 1
                    self._open[self._seq] = payloads[key]
                    return self._seq

                def ReadFile(self, handle, buffer, size, read, overlapped):
                    remaining = self._open.get(int(handle), b"")
                    chunk = remaining[: int(size)]
                    self._open[int(handle)] = remaining[len(chunk) :]
                    if chunk:
                        ctypes.memmove(buffer, chunk, len(chunk))
                    ctypes.cast(read, ctypes.POINTER(wintypes.DWORD)).contents.value = len(chunk)
                    return 1

                def CloseHandle(self, handle):
                    self._open.pop(int(handle), None)
                    return 1

            kernel = SharedKernel()
            real_open = open

            def locked_open(file, *args, **kwargs):
                name = os.fspath(file)
                if "preply-cookies-" not in name and Path(name).name.startswith("Cookies"):
                    raise PermissionError(
                        13,
                        "The process cannot access the file because it is being used by another process",
                        name,
                        32,
                    )
                return real_open(file, *args, **kwargs)

            def no_restart_manager():
                raise AssertionError("RmShutdown")

            with (
                patch.object(browser_login, "_kernel32", lambda: kernel),
                patch.object(browser_login, "_rstrtmgr", no_restart_manager),
                patch("builtins.open", locked_open),
            ):
                copy = browser_login._copy_sqlite(source)
            try:
                self.assertEqual(copy.read_bytes(), b"db-bytes")
                self.assertEqual(Path(str(copy) + "-wal").read_bytes(), b"wal-bytes")
                self.assertEqual(Path(str(copy) + "-shm").read_bytes(), b"shm-bytes")
                self.assertEqual(Path(str(copy) + "-journal").read_bytes(), b"journal-bytes")
            finally:
                shutil.rmtree(copy.parent, ignore_errors=True)
            names = [str(call["name"]) for call in kernel.calls]
            self.assertEqual(names, [str(source), str(source) + "-wal", str(source) + "-shm", str(source) + "-journal"])
            self.assertTrue(all(call["access"] == browser_login._GENERIC_READ for call in kernel.calls))
            self.assertTrue(all(call["share"] == browser_login._FILE_SHARE_ALL for call in kernel.calls))
            self.assertEqual(browser_login._FILE_SHARE_ALL, 0x1 | 0x2 | 0x4)
            self.assertTrue(all(call["disposition"] == browser_login._OPEN_EXISTING for call in kernel.calls))

    def test_sharing_violation_is_retried_then_names_the_holder(self) -> None:
        import ctypes

        from preply_extractor import browser_login
        from preply_extractor.browser_login import BrowserCookieError

        class Denied:
            def __init__(self) -> None:
                self.calls = 0
                self.flags: list[int] = []

            def CreateFileW(self, _name, _access, _share, _security, _disposition, flags, _template):
                self.calls += 1
                self.flags.append(int(flags))
                ctypes.set_last_error(32)
                return ctypes.c_void_p(-1).value

            def CloseHandle(self, _handle):
                return 1

        class RestartManager:
            def __init__(self) -> None:
                self.called: list[str] = []

            def RmStartSession(self, session, _flags, _key):
                self.called.append("RmStartSession")
                session.contents.value = 1
                return 0

            def RmRegisterResources(self, *_args):
                self.called.append("RmRegisterResources")
                return 0

            def RmGetList(self, _session, needed, count, infos, _reason):
                self.called.append("RmGetList")
                if infos is None:
                    needed.contents.value = 1
                    count.contents.value = 0
                    return 234
                count.contents.value = 1
                infos[0].strAppName = "Opera Internet Browser"
                return 0

            def RmEndSession(self, _session):
                self.called.append("RmEndSession")
                return 0

            def RmShutdown(self, *_args):
                self.called.append("RmShutdown")
                raise AssertionError("RmShutdown")

        denied = Denied()
        manager = RestartManager()
        sleeps: list[float] = []
        with (
            patch.object(browser_login, "_kernel32", lambda: denied),
            patch.object(browser_login, "_rstrtmgr", lambda: manager),
            patch("preply_extractor.browser_login.time.sleep", sleeps.append),
        ):
            with self.assertRaises(BrowserCookieError) as caught:
                browser_login._copy_bytes_shared(Path("Cookies"), Path("unused"))
        message = str(caught.exception)
        attempts = browser_login._SHARE_ATTEMPTS
        self.assertIn("being used by another process", message)
        self.assertIn("Cookies", message)
        self.assertIn("cookie database", message)
        self.assertIn("Opera Internet Browser", message)
        self.assertNotIn("not one this window can read cookies", message)
        self.assertEqual(denied.calls, attempts * 2)
        self.assertEqual(denied.flags[:attempts], [browser_login._FILE_ATTRIBUTE_NORMAL] * attempts)
        backup = browser_login._FILE_ATTRIBUTE_NORMAL | browser_login._FILE_FLAG_BACKUP_SEMANTICS
        self.assertEqual(denied.flags[attempts:], [backup] * attempts)
        self.assertEqual(sleeps, [browser_login._RETRY_DELAY_SECONDS] * ((attempts - 1) * 2))
        self.assertNotIn("RmShutdown", manager.called)
        self.assertIn("RmEndSession", manager.called)

    def test_open_uses_the_https_handler_and_not_a_custom_profile(self) -> None:
        from preply_extractor.browser_login import PREPLY_AUTH_URL, open_in_default_browser

        seen: list[str] = []
        open_in_default_browser(PREPLY_AUTH_URL, opener=seen.append)
        self.assertEqual(seen, [PREPLY_AUTH_URL])
        self.assertTrue(seen[0].startswith("https://"))
        self.assertNotIn("user-data-dir", seen[0])
        self.assertNotIn(".preply-profile", seen[0])

    def test_firefox_cookies_ignore_other_sites_and_require_a_session(self) -> None:
        import sqlite3

        from preply_extractor.browser_login import NoPreplySession, read_firefox_cookies, require_preply_session

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "cookies.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, path TEXT)")
            connection.executemany(
                "INSERT INTO moz_cookies VALUES (?, ?, ?, ?)",
                [
                    (".preply.com", "sessionid", "signed-in", "/"),
                    (".preply.com", "csrftoken", "token", "/"),
                    (".google.com", "SID", "other-site", "/"),
                ],
            )
            connection.commit()
            connection.close()
            cookies = read_firefox_cookies(database)
            names = [cookie["name"] for cookie in cookies]
            hosts = [str(cookie["domain"]) for cookie in cookies]
            self.assertEqual(names, ["sessionid", "csrftoken"])
            self.assertTrue(all(host.endswith("preply.com") for host in hosts))
            self.assertFalse(any("google.com" in host for host in hosts))
            self.assertNotIn("SID", names)
            self.assertEqual([cookie["name"] for cookie in require_preply_session(cookies)], names)
            with self.assertRaises(NoPreplySession):
                require_preply_session([cookie for cookie in cookies if cookie["name"] != "sessionid"])

    def test_firefox_profile_comes_from_profiles_ini(self) -> None:
        from preply_extractor.browser_login import _firefox_profile_dir

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "Profiles" / "abc.default-release"
            profile.mkdir(parents=True)
            (root / "Profiles" / "other").mkdir()
            ini = root / "profiles.ini"
            ini.write_text(
                "[Install1]\nDefault=Profiles/abc.default-release\nLocked=1\n\n"
                "[Profile0]\nName=other\nIsRelative=1\nPath=Profiles/other\nDefault=1\n",
                encoding="utf-8",
            )
            self.assertEqual(_firefox_profile_dir(ini), profile)

    def test_chromium_cookies_use_that_profiles_database(self) -> None:
        import sqlite3

        from preply_extractor.browser_login import read_chromium_cookies

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "Cookies"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT)"
            )
            connection.executemany(
                "INSERT INTO cookies VALUES (?, ?, ?, ?, ?)",
                [
                    (".preply.com", "sessionid", "signed-in", b"", "/"),
                    ("accounts.google.com", "SID", "other-site", b"", "/"),
                ],
            )
            connection.commit()
            connection.close()
            cookies = read_chromium_cookies(database, None)
            names = [cookie["name"] for cookie in cookies]
            hosts = [str(cookie["domain"]) for cookie in cookies]
            self.assertEqual(names, ["sessionid"])
            self.assertTrue(all(host.endswith("preply.com") for host in hosts))
            self.assertFalse(any("google" in host for host in hosts))

    def test_invalid_utf8_cookie_does_not_break_the_latin1_header(self) -> None:
        import requests

        from preply_extractor.browser_login import (
            UnreadableSessionCookie,
            _cookie_text,
            _decrypt_chromium_value,
            require_preply_session,
        )
        from preply_extractor.preply_api import PreplyError, RequestsSession
        from preply_extractor.ui_text import phrase_for_exception, phrase_text

        with patch("preply_extractor.browser_login._aes_gcm_decrypt", return_value=b"\x01\xff"):
            decoded = _decrypt_chromium_value(b"v10" + bytes(28), b"k" * 32)
        decoded.encode("latin-1")
        self.assertNotIn("\ufffd", decoded)
        self.assertEqual(_cookie_text(b"\x01\xff"), decoded)

        seen: dict[str, str] = {}

        class Adapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                header = request.headers.get("Cookie", "")
                header.encode("latin-1")
                seen["cookie"] = header
                response = requests.Response()
                response.status_code = 200
                response._content = b"{}"
                response.url = request.url
                response.headers["Content-Type"] = "application/json"
                return response

            def close(self) -> None:
                return None

        session = RequestsSession(
            [
                {"domain": ".preply.com", "name": "sessionid", "value": "kept", "path": "/"},
                {"domain": ".preply.com", "name": "broken", "value": "\ufffd", "path": "/"},
                {"domain": ".preply.com", "name": "raw", "value": decoded, "path": "/"},
            ],
            "en",
        )
        client = session._client()
        client.mount("https://", Adapter())
        session.post_json("/graphql/v2/VocabBankOverview", {})
        header = seen["cookie"]
        self.assertIn("sessionid=", header)
        self.assertIn("raw=", header)
        self.assertNotIn("broken=", header)
        self.assertNotIn("\ufffd", header)
        self.assertNotIn("latin-1", header)
        kept = require_preply_session(
            [
                {"domain": ".preply.com", "name": "sessionid", "value": "kept", "path": "/"},
                {"domain": ".preply.com", "name": "broken", "value": "\ufffd", "path": "/"},
                {"domain": ".preply.com", "name": "raw", "value": decoded, "path": "/"},
            ]
        )
        self.assertEqual([cookie["name"] for cookie in kept], ["sessionid", "raw"])
        for cookie in kept:
            cookie["value"].encode("latin-1")

        with self.assertRaises(UnreadableSessionCookie):
            require_preply_session(
                [{"domain": ".preply.com", "name": "sessionid", "value": "\ufffd", "path": "/"}]
            )
        wrapped = PreplyError(
            "Не удалось прочитать профиль. Список слов не загружен. "
            "'latin-1' codec can't encode character '\\ufffd' in position 1: ordinal not in range(256)"
        )
        shown = phrase_text(phrase_for_exception(wrapped))
        self.assertNotIn("ufffd", shown)
        self.assertNotIn("latin-1", shown)
        self.assertNotIn("Traceback", shown)
        self.assertIn("preply.com", shown)

    def test_host_hash_prefix_is_not_sent_as_the_cookie(self) -> None:
        import hashlib
        import sqlite3

        from preply_extractor.browser_login import _strip_host_bound_prefix, read_chromium_cookies

        host = "preply.com"
        prefix = hashlib.sha256(host.encode("utf-8")).digest()
        self.assertEqual(_strip_host_bound_prefix(prefix + b"session-ok", host), b"session-ok")
        self.assertEqual(_strip_host_bound_prefix(b"plain-value", host), b"plain-value")

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "Cookies"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT)"
            )
            connection.execute(
                "INSERT INTO cookies VALUES (?, ?, ?, ?, ?)",
                (host, "sessionid", "", b"encrypted", "/"),
            )
            connection.commit()
            connection.close()
            with patch(
                "preply_extractor.browser_login._decrypt_chromium_bytes",
                return_value=prefix + b"session-ok",
            ):
                cookies = read_chromium_cookies(database, b"k" * 32)
        self.assertEqual([cookie["name"] for cookie in cookies], ["sessionid"])
        self.assertEqual(cookies[0]["value"], "session-ok")
        self.assertEqual(len(cookies[0]["value"]), len("session-ok"))

    def test_profile_request_sends_site_headers_and_explains_400(self) -> None:
        import requests

        from preply_extractor.preply_api import PreplyError, RequestsSession, fetch_learner_profile

        session = RequestsSession(
            [
                {"domain": "preply.com", "name": "sessionid", "value": "session-test", "path": "/"},
                {"domain": "preply.com", "name": "csrftoken", "value": "csrf-test", "path": "/"},
                {"domain": "preply.com", "name": "empty", "value": "", "path": "/"},
            ],
            "en",
        )
        client = session._client()
        self.assertEqual(client.headers.get("Origin"), "https://preply.com")
        self.assertEqual(client.headers.get("Referer"), "https://preply.com/en/practice/vocab-bank")
        self.assertEqual(client.headers.get("Content-Type"), "application/json")
        self.assertEqual(client.headers.get("x-csrftoken"), "csrf-test")
        self.assertNotIn("empty", {cookie.name for cookie in client.cookies})

        class Adapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                seen["origin"] = request.headers.get("Origin", "")
                seen["referer"] = request.headers.get("Referer", "")
                seen["csrf"] = request.headers.get("x-csrftoken", "")
                response = requests.Response()
                response.status_code = 400
                response._content = (
                    b'{"errors":[{"message":"Bad request token abcdefghijklmnopqrstuvwxyz012345"}]}'
                )
                response.url = request.url
                response.headers["Content-Type"] = "application/json"
                return response

            def close(self) -> None:
                return None

        seen: dict[str, str] = {}
        client.mount("https://", Adapter())
        with self.assertRaises(PreplyError) as caught:
            fetch_learner_profile(session.post_json)
        message = str(caught.exception)
        self.assertEqual(seen["origin"], "https://preply.com")
        self.assertIn("vocab-bank", seen["referer"])
        self.assertEqual(seen["csrf"], "csrf-test")
        self.assertIn("HTTP 400", message)
        self.assertIn("Bad request token", message)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", message)

    def test_missing_session_cookie_does_not_open_the_opera_file(self) -> None:
        _app()
        from preply_extractor.browser_login import NoPreplySession

        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        login = _open_login_window(missing_session=True)
        worker._login = login
        with (
            patch("preply_extractor.browser_login.chromium_cookie_database", side_effect=AssertionError("opera cookies")),
            patch("preply_extractor.browser_login._copy_bytes_shared", side_effect=AssertionError("opera cookies")),
            patch("os.startfile", side_effect=AssertionError("tab")),
            patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
        ):
            worker._connect()
        launched.assert_not_called()
        self.assertFalse(login.closed)
        self.assertEqual(len(failed), 1)
        self.assertIn("preply.com", failed[0])
        self.assertIn("Я вошёл", failed[0])
        self.assertNotIn("signed-in", failed[0])


class ChromeProfileTests(unittest.TestCase):
    def test_installed_chrome_selects_the_chrome_channel(self) -> None:
        with patch("preply_extractor.preply_api.Path.is_file", return_value=True):
            self.assertEqual(preferred_browser_channel(), "chrome")

    def test_missing_chrome_keeps_playwright_chromium(self) -> None:
        with patch("preply_extractor.preply_api.Path.is_file", return_value=False):
            self.assertIsNone(preferred_browser_channel())

    def test_program_profile_is_not_the_system_chrome_directory(self) -> None:
        from preply_extractor.gui import PROFILE_DIR

        text = str(PROFILE_DIR).replace("\\", "/").lower()
        self.assertEqual(text, ".preply-profile")
        self.assertNotIn("user data", text)
        self.assertNotIn("google", text)
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".preply-profile/", ignore)

    def test_launch_is_a_persistent_chrome_context_on_the_program_profile(self) -> None:
        calls: dict[str, object] = {}

        class Page:
            def goto(self, *_args, **_kwargs):
                return None

        class Context:
            pages = [Page()]

            def close(self):
                return None

        class Chromium:
            def launch_persistent_context(self, user_data_dir, **kwargs):
                calls["dir"] = user_data_dir
                calls["kwargs"] = kwargs
                return Context()

            def launch(self, **_kwargs):
                raise AssertionError("a fresh browser context must not be used")

        class Playwright:
            chromium = Chromium()

            def stop(self):
                return None

        class Started:
            def start(self):
                return Playwright()

        profile = str(ROOT / ".preply-profile")
        with (
            patch("playwright.sync_api.sync_playwright", return_value=Started()),
            patch.object(BrowserSession, "_wait_until_logged_in", lambda _self: None),
        ):
            session = BrowserSession(profile, "en", "", False, 1, channel="chrome")
            session.__enter__()
            session.__exit__(None, None, None)
        self.assertEqual(calls["dir"], profile)
        kwargs = calls["kwargs"]
        assert isinstance(kwargs, dict)
        self.assertEqual(kwargs.get("channel"), "chrome")
        self.assertNotIn("User Data", str(calls["dir"]))

    def test_open_browser_session_passes_chrome_and_the_given_directory(self) -> None:
        seen: dict[str, object] = {}

        def fake_enter(self):
            seen["channel"] = self.channel
            seen["dir"] = self.user_data_dir
            return self

        with (
            patch("preply_extractor.deck_job.preferred_browser_channel", return_value="chrome"),
            patch.object(BrowserSession, "__enter__", fake_enter),
        ):
            open_browser_session(ROOT / ".preply-profile", "en", "")
        self.assertEqual(seen["channel"], "chrome")
        self.assertTrue(str(seen["dir"]).endswith(".preply-profile"))
        self.assertNotIn("User Data", str(seen["dir"]))


class WindowMotionTests(unittest.TestCase):
    def test_saved_normal_rect_and_maximize_target(self) -> None:
        from PySide6.QtCore import QRect

        from preply_extractor.gui import MOTION_MS, maximize_geometry, taskbar_target_rect

        self.assertGreaterEqual(MOTION_MS, 180)
        self.assertLessEqual(MOTION_MS, 220)
        button = QRect(988, 912, 44, 48)
        bar = QRect(0, 912, 1536, 48)
        self.assertEqual(taskbar_target_rect(button, bar), button)
        fallback = taskbar_target_rect(None, bar)
        self.assertTrue(bar.contains(fallback.center()))
        self.assertLess(abs(fallback.center().x() - bar.center().x()), 2)
        self.assertNotEqual(fallback.left(), bar.left())
        self.assertEqual(taskbar_target_rect(None, None).width(), 0)
        screen = QRect(0, 0, 1920, 1040)
        self.assertEqual(maximize_geometry(screen), screen)
        normal = QRect(80, 60, 920, 700)

        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    window.setGeometry(normal)
                    window._motion.remember_normal()
                    self.assertEqual(window._motion.normal_rect(), normal)
                    self.assertIsNone(window._motion.mode)
                finally:
                    window.close()

    def test_minimize_keeps_the_normal_restore_rect(self) -> None:
        from PySide6.QtCore import QRect
        from PySide6.QtTest import QTest

        from preply_extractor.gui import MOTION_MS

        normal = QRect(80, 60, 920, 700)
        dock = QRect(1200, 980, 44, 40)
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    window.setGeometry(normal)
                    window._motion.remember_normal()
                    window._motion._dock_rect = lambda: QRect(dock)
                    window.minimize_button.click()
                    self.assertEqual(window.geometry(), normal)
                    self.assertNotEqual(window.geometry(), dock)
                    QTest.qWait(MOTION_MS + 120)
                    self.assertEqual(window._motion.normal_rect(), normal)
                    self.assertNotEqual(window._motion.normal_rect(), dock)
                    self.assertNotEqual(window.geometry(), dock)
                    self.assertEqual(window.geometry().size(), normal.size())
                    window._motion.restore_from_shell()
                    self.assertFalse(window.isMinimized())
                    self.assertEqual(window.geometry(), normal)
                    self.assertEqual(window.windowOpacity(), 1.0)
                    self.assertIsNone(window._motion._snapshot)
                finally:
                    window.close()

    def test_shell_commands_toggle_and_keep_the_saved_rect(self) -> None:
        from PySide6.QtCore import QRect
        from PySide6.QtTest import QTest

        from preply_extractor.gui import MOTION_MS, _SC_MINIMIZE, _SC_RESTORE, logical_taskbar_rect

        physical = QRect(1976, 1824, 88, 96)
        screen = QRect(0, 0, 1536, 960)
        self.assertEqual(logical_taskbar_rect(physical, 2.0, screen), QRect(988, 912, 44, 48))
        self.assertEqual(logical_taskbar_rect(QRect(988, 912, 44, 48), 2.0, screen), QRect(988, 912, 44, 48))

        normal = QRect(80, 60, 920, 700)
        button = QRect(640, 900, 44, 48)
        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    window.setGeometry(normal)
                    window._motion.remember_normal()
                    with patch("preply_extractor.gui.find_taskbar_button_rect", return_value=QRect(button)):
                        self.assertEqual(window._motion._dock_rect(), button)
                    window._motion._dock_rect = lambda: QRect(button)
                    self.assertTrue(window._handle_shell_command(_SC_MINIMIZE))
                    self.assertEqual(window._motion.mode, "minimize")
                    self.assertEqual(window._motion.normal_rect(), normal)
                    self.assertTrue(window._handle_shell_command(_SC_RESTORE))
                    self.assertEqual(window._motion.mode, "minimize")
                    QTest.qWait(MOTION_MS + 150)
                    self.assertEqual(window._motion.normal_rect(), normal)
                    self.assertNotEqual(window.geometry(), button)
                    self.assertEqual(window.geometry().size(), normal.size())
                    self.assertTrue(window._handle_shell_command(_SC_RESTORE))
                    self.assertFalse(window.isMinimized())
                    self.assertEqual(window.geometry(), normal)
                    self.assertEqual(window.windowOpacity(), 1.0)
                    self.assertTrue(window._handle_shell_command(_SC_MINIMIZE))
                    self.assertFalse(window.isMinimized())
                    self.assertEqual(window.geometry(), normal)
                finally:
                    window.close()


class ComponentRowTests(unittest.TestCase):
    def test_each_component_can_show_pending_ok_and_failed(self) -> None:
        from preply_extractor.gui import DependencyPanel
        from preply_extractor.gui_logic import DEPENDENCY_ORDER

        _app()
        panel = DependencyPanel()
        try:
            self.assertEqual(tuple(panel.rows), DEPENDENCY_ORDER)
            for key in DEPENDENCY_ORDER:
                row = panel.rows[key]
                self.assertEqual(row._state, "pending")
                self.assertFalse(row.spinner.isHidden())
                self.assertTrue(row.mark.isHidden())
            panel.show_report(
                {"pyside6": True, "requests": True, "playwright": False, "chromium": False},
                repair=False,
            )
            self.assertEqual(panel.rows["requests"]._state, "ok")
            self.assertEqual(panel.rows["requests"].mark.text(), "✓")
            self.assertTrue(panel.rows["requests"].spinner.isHidden())
            self.assertEqual(panel.rows["chromium"]._state, "failed")
            self.assertEqual(panel.rows["chromium"].mark.text(), "✕")
            self.assertFalse(panel.install_button.isHidden())
            panel.begin_checks()
            for key in DEPENDENCY_ORDER:
                self.assertEqual(panel.rows[key]._state, "pending")
                self.assertFalse(panel.rows[key].spinner.isHidden())
        finally:
            panel.close()


class FramelessWindowTests(unittest.TestCase):
    def test_header_drags_and_close_uses_the_quit_path(self) -> None:
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    self.assertTrue(bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint))
                    self.assertEqual(window.windowTitle(), "Словарь Preply в Anki")
                    self.assertTrue(window.minimize_button.isVisible())
                    self.assertTrue(window.maximize_button.isVisible())
                    self.assertTrue(window.close_button.isVisible())
                    self.assertEqual(window.settings_button.objectName(), "settingsButton")
                    header_buttons = (
                        window.settings_button,
                        window.minimize_button,
                        window.maximize_button,
                        window.close_button,
                    )
                    shared = header_buttons[0].size()
                    self.assertEqual(shared.width(), 36)
                    self.assertEqual(shared.height(), 32)
                    from preply_extractor.gui import _SETTINGS_ICON_PATH

                    self.assertTrue(_SETTINGS_ICON_PATH.is_file())
                    for button in header_buttons:
                        self.assertEqual(button.size(), shared)
                        self.assertEqual(button.toolTip(), "")
                        self.assertEqual(button.text(), "")
                        self.assertFalse(button.icon().isNull())
                        if button is window.settings_button:
                            self.assertLessEqual(button.iconSize().width(), shared.width())
                            self.assertLessEqual(button.iconSize().height(), shared.height())
                            self.assertGreater(button.iconSize().width(), 0)
                        else:
                            self.assertEqual(button.iconSize().width(), 16)
                            self.assertEqual(button.iconSize().height(), 16)
                    with patch.object(window, "_open_settings") as open_settings:
                        window.settings_button.click()
                    open_settings.assert_called_once()
                    self.assertEqual(window.settings_button.property("chromeGlyph"), "settings")

                    window.move(80, 60)
                    local = QPointF(12, 10)
                    start = QPointF(200, 150)
                    press = QMouseEvent(
                        QEvent.Type.MouseButtonPress,
                        local,
                        start,
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    window.title_bar.mousePressEvent(press)
                    moved = QMouseEvent(
                        QEvent.Type.MouseMove,
                        local + QPointF(40, 15),
                        start + QPointF(40, 15),
                        Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    window.title_bar.mouseMoveEvent(moved)
                    self.assertEqual(window.pos().x(), 120)
                    self.assertEqual(window.pos().y(), 75)

                    from PySide6.QtTest import QTest

                    from preply_extractor.gui import MOTION_MS

                    window.maximize_button.click()
                    QTest.qWait(MOTION_MS + 80)
                    self.assertTrue(window.isMaximized())
                    self.assertEqual(window.maximize_button.toolTip(), "")
                    self.assertEqual(window.maximize_button.text(), "")
                    self.assertEqual(window.maximize_button.property("chromeGlyph"), "restore")
                    self.assertEqual(window.corner_radius(), 0)
                    window.maximize_button.click()
                    QTest.qWait(MOTION_MS + 80)
                    self.assertFalse(window.isMaximized())
                    self.assertEqual(window.maximize_button.property("chromeGlyph"), "maximize")
                    self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
                    self.assertTrue(window.mask().isEmpty())
                    self.assertEqual(window.corner_radius(), 16)

                    window.showMaximized()
                    self.assertEqual(window.corner_radius(), 0)
                    self.assertTrue(window.mask().isEmpty())
                    window.showNormal()
                    self.assertEqual(window.corner_radius(), 16)
                    self.assertTrue(window.mask().isEmpty())

                    closed: list[object] = []

                    def fake_close(self, event) -> None:
                        closed.append(self)
                        event.accept()

                    with patch.object(MainWindow, "closeEvent", fake_close):
                        window.close_button.click()
                    self.assertEqual(closed, [window])
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
