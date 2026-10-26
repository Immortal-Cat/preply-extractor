"""Wizard selection and save-path checks. No Preply session required."""

from __future__ import annotations

import json
import os
import sys
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
    WordListPanel,
    russian_error,
)
from preply_extractor.gui_logic import (
    BrowserInstallError,
    ensure_playwright_chromium,
    format_profile,
    install_chromium_command,
    join_deck_path,
    missing_browser_message,
    vocabulary_alias,
)
from preply_extractor.preply_api import CATEGORIES, PreplyError, _payload, fetch_vocabulary, parse_learner_profile
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
        worker.connect_account()
        self.assertEqual(worker._commands.get_nowait()[0], "connect")
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

    def test_retry_button_invokes_its_slot(self) -> None:
        _app()
        panel = LoginPanel()
        calls: list[str] = []
        panel.retry_button.clicked.connect(lambda _checked=False: calls.append("retry"))
        panel.retry_button.click()
        self.assertEqual(calls, ["retry"])
        self.assertTrue(panel.retry_button.isEnabled())
        panel.close()

    def test_retry_installs_when_browser_is_missing_then_logs_in(self) -> None:
        _app()
        worker = ExportWorker()
        order: list[str] = []

        def exists() -> bool:
            order.append("check")
            return False

        def run_install(*_args, **_kwargs):
            order.append("install")
            completed = type("Completed", (), {})()
            completed.returncode = 0
            completed.stderr = ""
            completed.stdout = ""
            return completed

        def open_session(*_args, **_kwargs):
            order.append("login")
            session = type("Session", (), {})()
            session.endpoint = "/graphql/v2"
            session.post_json = lambda *_a, **_k: (200, "{}")
            session.close = lambda: None
            return session

        def load_profile(*_args, **_kwargs):
            order.append("profile")
            return object()

        ready: list[object] = []
        worker.profile_ready.connect(ready.append)
        with (
            patch("preply_extractor.gui.chromium_executable_exists", side_effect=exists),
            patch("preply_extractor.gui.subprocess.run", side_effect=run_install) as run,
            patch("preply_extractor.gui.open_browser_session", side_effect=open_session),
            patch("preply_extractor.gui.fetch_learner_profile", side_effect=load_profile),
            patch("preply_extractor.gui.fetch_vocabulary") as vocab,
        ):
            worker.retry_connection()
            command, _payload = worker._commands.get_nowait()
            self.assertEqual(command, "retry")
            worker._close_session()
            worker._connect()
            self.assertEqual(run.call_args.args[0], install_chromium_command())
        self.assertEqual(order, ["check", "install", "login", "profile"])
        self.assertEqual(len(ready), 1)
        vocab.assert_not_called()

    def test_retry_skips_install_when_browser_exists_and_logs_in(self) -> None:
        _app()
        worker = ExportWorker()
        order: list[str] = []

        def open_session(*_args, **_kwargs):
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
            patch("preply_extractor.gui.chromium_executable_exists", return_value=True),
            patch("preply_extractor.gui.subprocess.run") as run,
            patch("preply_extractor.gui.open_browser_session", side_effect=open_session),
            patch("preply_extractor.gui.fetch_learner_profile", side_effect=load_profile),
            patch("preply_extractor.gui.fetch_vocabulary") as vocab,
        ):
            worker._close_session()
            worker._connect()
        run.assert_not_called()
        self.assertEqual(order, ["login", "profile"])
        vocab.assert_not_called()

    def test_failed_install_can_be_retried(self) -> None:
        _app()
        worker = ExportWorker()
        failed: list[str] = []
        worker.connect_failed.connect(failed.append)

        def run_install(*_args, **_kwargs):
            completed = type("Completed", (), {})()
            completed.returncode = 1
            completed.stderr = "network down\nplaywright install chromium"
            completed.stdout = ""
            return completed

        with (
            patch("preply_extractor.gui.chromium_executable_exists", return_value=False),
            patch("preply_extractor.gui.subprocess.run", side_effect=run_install),
            patch("preply_extractor.gui.open_browser_session") as opened,
        ):
            worker._connect()
        self.assertEqual(len(failed), 1)
        self.assertNotIn("playwright install", failed[0].lower())
        self.assertIn("Повторить", failed[0])
        self.assertIn("network down", failed[0])
        opened.assert_not_called()
        worker.retry_connection()
        self.assertEqual(worker._commands.get_nowait()[0], "retry")


if __name__ == "__main__":
    unittest.main(verbosity=2)
