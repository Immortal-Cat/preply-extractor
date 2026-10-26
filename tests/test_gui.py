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

        def session_from_cookies(_cookies, _language):
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"
            session.post_json = lambda *_a, **_k: (200, "{}")
            session.close = lambda: None
            return session

        with (
            patch("preply_extractor.gui.subprocess.run") as run,
            patch("preply_extractor.gui.load_default_browser_preply_cookies", return_value=[{"name": "sessionid", "domain": ".preply.com", "path": "/", "value": "present"}]) as loaded,
            patch("preply_extractor.gui.BrowserCookieSession", side_effect=session_from_cookies),
            patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
            patch("preply_extractor.gui.fetch_learner_profile", return_value=object()),
            patch("preply_extractor.gui.fetch_vocabulary") as vocab,
        ):
            worker.use_browser_cookies()
            self.assertEqual(worker._commands.get_nowait()[0], "cookies")
            worker._connect()
        run.assert_not_called()
        loaded.assert_called_once()
        launched.assert_not_called()
        self.assertEqual(len(ready), 1)
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

        with (
            patch("preply_extractor.gui.subprocess.run") as run,
            patch("preply_extractor.gui.load_default_browser_preply_cookies", return_value=[{"name": "sessionid", "domain": ".preply.com", "path": "/", "value": "present"}]),
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

    def test_startup_check_does_not_open_a_browser_until_authorize(self) -> None:
        _app()
        from preply_extractor.browser_login import PREPLY_AUTH_URL

        opened: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with (
                patch.object(ExportWorker, "start", lambda *_args, **_kwargs: None),
                patch("preply_extractor.gui.open_in_default_browser", side_effect=opened.append),
                patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
            ):
                window = MainWindow(settings_file=path)
                window.show()
                try:
                    self.assertEqual(window.stack.currentIndex(), _STEP_DEPS)
                    self.assertEqual(window.worker._commands.get_nowait()[0], "check")
                    self.assertFalse(hasattr(window, "theme_combo"))
                    launched.assert_not_called()
                    self.assertEqual(opened, [])
                    window.login_panel.authorize_button.click()
                    self.assertEqual(opened, [PREPLY_AUTH_URL])
                    self.assertTrue(opened[0].startswith("https://"))
                    self.assertFalse(window.login_panel.continue_button.isHidden())
                    self.assertTrue(window.worker._commands.empty())
                    launched.assert_not_called()
                    window.login_panel.continue_button.click()
                    self.assertEqual(window.worker._commands.get_nowait()[0], "cookies")
                    launched.assert_not_called()
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
                    self.assertIn("Playwright: не установлено", window.deps_panel.lines.text())
                    self.assertIn("Chromium: не установлено", window.deps_panel.lines.text())
                    launched.assert_not_called()
                    window.worker._commands.get_nowait()
                    window.deps_panel.install_button.click()
                    self.assertEqual(window.worker._commands.get_nowait()[0], "install")
                    launched.assert_not_called()
                finally:
                    window.close()


class DefaultBrowserTests(unittest.TestCase):
    def test_progid_selects_the_cookie_store(self) -> None:
        from preply_extractor.browser_login import browser_family

        self.assertEqual(browser_family("FirefoxURL-308046B0AF4A39CB"), "firefox")
        self.assertEqual(browser_family("ChromeHTML"), "chrome")
        self.assertEqual(browser_family("MSEdgeHTM"), "edge")

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

    def test_missing_session_cookie_does_not_open_playwright(self) -> None:
        _app()
        from preply_extractor.browser_login import NoPreplySession

        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)
        with (
            patch(
                "preply_extractor.gui.load_default_browser_preply_cookies",
                side_effect=NoPreplySession("no preply.com session cookie"),
            ),
            patch("preply_extractor.preply_api.BrowserSession.__enter__") as launched,
        ):
            worker._connect()
        launched.assert_not_called()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
