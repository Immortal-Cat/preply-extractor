"""Russian desktop wizard for the Preply vocabulary export."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from preply_extractor.anki_package import build_apkg
from preply_extractor.app_settings import (
    AppSettings,
    existing_directory,
    load_settings,
    save_settings,
    settings_path,
)
from preply_extractor.audio import AudioFailure
from preply_extractor.browser_login import (
    PREPLY_AUTH_URL,
    BrowserCookieSession,
    LoginWindowClosed,
    NoPreplySession,
    SeparateProfileWindow,
    context_was_closed,
)
from preply_extractor.cli import DEFAULT_DECK
from preply_extractor.deck_job import collect_audio
from preply_extractor.gui_logic import (
    DEPENDENCY_ORDER,
    BrowserInstallError,
    _install_failure_detail,
    dependencies_ready,
    inspect_dependencies,
    install_missing_dependencies,
    join_deck_path,
    language_label,
    language_plan,
    vocabulary_alias,
    word_key,
)
from preply_extractor.preply_api import (
    LearnerProfile,
    SessionCancelled,
    fetch_learner_profile,
    fetch_vocabulary,
)
from preply_extractor.settings_dialog import SettingsDialog
from preply_extractor.themes import DEFAULT_THEME, apply_theme
from preply_extractor.ui_text import (
    display_word,
    phrase_for_exception,
    phrase_text,
    profile_text,
    set_language,
    state_title,
    tr,
)
from preply_extractor.words import VocabWord

PROFILE_DIR = Path(".preply-profile")


def program_profile_directory() -> Path:
    """This program's profile, next to the project. Not Opera's or Chrome's user data."""
    return (Path(__file__).resolve().parent.parent / PROFILE_DIR).resolve()
PAGE_SIZE = 20
PAGE_DELAY = 0.25
UI_LANGUAGE = "en"
_STEP_DEPS = 0
_STEP_LOGIN = 1
_STEP_LANGUAGE = 2
_STEP_WORDS = 3
_STEP_BUILD = 4
_STEP_SAVE = 5


def russian_error(exc: BaseException) -> str:
    """User-visible error in the active interface language. Russian is the default."""
    return phrase_text(phrase_for_exception(exc))


def _pop(bucket: queue.Queue):
    try:
        return bucket.get_nowait()
    except queue.Empty:
        return None


def default_save_folder() -> Path:
    documents = Path.home() / "Documents"
    if documents.is_dir():
        return documents
    return Path.cwd()


class ExportWorker(QThread):
    """Owns the Playwright window and never touches Qt widgets."""

    status_changed = Signal(str, str)
    words_ready = Signal(object)
    load_failed = Signal(str)
    profile_ready = Signal(object)
    connect_failed = Signal(str)
    dependencies_checked = Signal(object)
    dependency_failed = Signal(str)
    build_progress = Signal(int, int, str)
    build_ready = Signal(str, object)
    build_failed = Signal(str)
    login_window_opened = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._commands: queue.Queue = queue.Queue()
        self._status_queue: queue.Queue = queue.Queue()
        self._load_failed_queue: queue.Queue = queue.Queue()
        self._connect_failed_queue: queue.Queue = queue.Queue()
        self._dependency_failed_queue: queue.Queue = queue.Queue()
        self._build_failed_queue: queue.Queue = queue.Queue()
        self._progress_queue: queue.Queue = queue.Queue()
        self._session = None
        self._login: SeparateProfileWindow | None = None
        self._stop = False

    def stopping(self) -> bool:
        return self._stop

    def _emit_status(self, state: str, phrase: tuple) -> None:
        self._status_queue.put((state, phrase))
        self.status_changed.emit(state, phrase_text(phrase))

    def _emit_failure(self, signal: Signal, bucket: queue.Queue, phrase: tuple) -> None:
        bucket.put(phrase)
        signal.emit(phrase_text(phrase))

    def _emit_progress(self, done: int, total: int, phrase: tuple) -> None:
        self._progress_queue.put(phrase)
        self.build_progress.emit(done, total, phrase_text(phrase))

    def check_dependencies(self) -> None:
        """Queue a dependency report. Does not open a browser."""
        self._commands.put(("check", None))

    def install_dependencies(self) -> None:
        """Queue installation of whatever the report marks missing. Does not open a browser."""
        self._commands.put(("install", None))

    def open_login_window(self) -> None:
        """Queue the separate profile window. Does not use the default browser."""
        self._commands.put(("open-login", None))

    def use_browser_cookies(self) -> None:
        """Read cookies from the separate sign-in window. Does not open Opera's cookie file."""
        self._commands.put(("cookies", None))

    def retry_connection(self) -> None:
        """Close any half-open session, install Chromium if it is missing, then log in again."""
        self._commands.put(("retry", None))

    def load(self, subject: str) -> None:
        self._commands.put(("load", subject.strip().lower()))

    def build(self, words: list[VocabWord]) -> None:
        self._commands.put(("build", list(words)))

    def request_stop(self) -> None:
        self._stop = True
        self._commands.put(("quit", None))

    def release_session(self) -> None:
        """Close an app-owned session, if one is open. Does not stop the worker."""
        self._commands.put(("release", None))

    def run(self) -> None:
        while True:
            command, payload = self._commands.get()
            if command == "quit" or self._stop:
                self._close_session()
                self._close_login_window()
                return
            try:
                if command == "check":
                    self._check_dependencies()
                elif command == "install":
                    self._install_dependencies()
                elif command == "open-login":
                    self._open_login()
                elif command in {"cookies", "connect", "retry"}:
                    if command == "retry":
                        self._close_session()
                    self._connect()
                elif command == "load":
                    self._load(str(payload))
                elif command == "release":
                    self._close_session()
                    self._close_login_window()
                elif command == "build":
                    self._build(list(payload))
            except Exception as exc:
                self._close_session()
                if command in {"check", "install"}:
                    self._report_dependency_error(exc)
                else:
                    self._report_worker_error(exc)

    def _report_dependency_error(self, exc: BaseException) -> None:
        if self._stop:
            return
        self._emit_dependency_failure(self._dependency_phrase(exc))

    def _dependency_phrase(self, exc: BaseException) -> tuple:
        if isinstance(exc, BrowserInstallError):
            reason = _install_failure_detail(exc.output or str(exc))
            if reason:
                return ("key", "deps_install_failed", {"reason": reason})
            return ("key", "deps_install_failed_plain", {})
        return phrase_for_exception(exc)

    def _emit_dependency_failure(self, phrase: tuple) -> None:
        self._dependency_failed_queue.put(phrase)
        self.dependency_failed.emit(phrase_text(phrase))

    def _report_worker_error(self, exc: BaseException) -> None:
        """Keep installer and login failures in the window. pythonw hides stderr."""
        if self._stop:
            return
        try:
            self._emit_failure(self.connect_failed, self._connect_failed_queue, phrase_for_exception(exc))
        except Exception:
            self.connect_failed.emit(str(exc).strip() or "error")

    def _check_dependencies(self) -> None:
        if self._stop:
            return
        self._emit_status("checking", ("key", "checking_components", {}))
        report = inspect_dependencies()
        if self._stop:
            return
        self.dependencies_checked.emit(report)

    def _install_dependencies(self) -> None:
        if self._stop:
            return
        try:
            report = inspect_dependencies()

            def run(command: list[str]) -> tuple[int, str]:
                if self._stop:
                    raise SessionCancelled("closed")
                name = "Chromium" if command[-1] == "chromium" else command[-1].split("==", 1)[0]
                self._emit_status("installing", ("key", "installing_named", {"name": name}))
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=flags,
                )
                output = "\n".join(
                    part.strip() for part in (completed.stderr, completed.stdout) if part and part.strip()
                )
                return completed.returncode, output

            install_missing_dependencies(report, run)
        except SessionCancelled:
            return
        except Exception as exc:
            if not self._stop:
                try:
                    self.dependencies_checked.emit(inspect_dependencies())
                except Exception:
                    pass
                self._report_dependency_error(exc)
            return
        if self._stop:
            return
        fresh = inspect_dependencies()
        self.dependencies_checked.emit(fresh)
        if not dependencies_ready(fresh):
            self._emit_dependency_failure(("key", "deps_still_missing", {}))

    def _open_login(self) -> None:
        if self._stop:
            return
        try:
            if self._login is None:
                self._login = SeparateProfileWindow(program_profile_directory())
            self._login.open(PREPLY_AUTH_URL)
        except Exception as exc:
            if context_was_closed(exc):
                self._fail_closed_window()
                return
            if self._login is not None and not self._login.is_open:
                self._close_login_window()
            if not self._stop:
                self._emit_failure(self.connect_failed, self._connect_failed_queue, phrase_for_exception(exc))
            return
        if not self._stop:
            self.login_window_opened.emit()

    def _connect(self) -> None:
        if self._stop:
            return
        try:
            login = self._login
            if login is None:
                raise NoPreplySession("no preply.com session cookie")
            if not login.is_open:
                raise LoginWindowClosed("sign-in window was closed")
            cookies = login.read_preply_cookies()
            if self._stop:
                return
            self._close_session()
            self._session = BrowserCookieSession(cookies, UI_LANGUAGE)
            self._emit_status("connected", ("key", "reading_profile", {}))
            profile = fetch_learner_profile(self._session.post_json, endpoint=self._session.endpoint)
            self._close_login_window()
            if self._stop:
                return
            self.profile_ready.emit(profile)
        except NoPreplySession:
            if not self._stop:
                self._emit_failure(self.connect_failed, self._connect_failed_queue, ("key", "login_not_finished", {}))
        except SessionCancelled:
            return
        except Exception as exc:
            if context_was_closed(exc):
                self._fail_closed_window()
                return
            self._close_session()
            if not self._stop:
                self._emit_failure(self.connect_failed, self._connect_failed_queue, phrase_for_exception(exc))

    def _load(self, subject: str) -> None:
        if self._stop:
            return
        if not subject:
            self._emit_failure(self.load_failed, self._load_failed_queue, ("key", "subject_missing", {}))
            return
        try:
            self._ensure_session()
            if self._stop or self._session is None:
                return
            self._emit_status("connected", ("key", "loading_vocab", {}))
            words = fetch_vocabulary(
                self._session.post_json,
                subject=subject,
                language_code=UI_LANGUAGE,
                page_size=PAGE_SIZE,
                delay_seconds=PAGE_DELAY,
                on_page=self._on_page,
            )
            if self._stop:
                return
            if words:
                phrase = ("key", "words_loaded", {"count": len(words)})
            else:
                phrase = ("key", "no_saved_words", {})
            self._emit_status("connected", phrase)
            self.words_ready.emit(words)
        except SessionCancelled:
            return
        except Exception as exc:
            self._close_session()
            if not self._stop:
                self._emit_failure(self.load_failed, self._load_failed_queue, phrase_for_exception(exc))

    def _ensure_session(self) -> None:
        """Keep the requests client that was built from the separate window."""
        if self._session is not None:
            return
        raise RuntimeError("Нет подключения к Preply.")

    def _on_page(self, label: str, page: int, count: int) -> None:
        self._emit_status("connected", ("key", "category_page", {"label": label, "page": page, "count": count}))

    def _build(self, words: list[VocabWord]) -> None:
        directory: Path | None = None
        try:
            if self._session is None:
                raise RuntimeError("Нет подключения к Preply.")
            total = len(words)

            def on_progress(index: int, count: int, lexeme: str) -> None:
                if self._stop:
                    raise SessionCancelled("closed")
                self._emit_progress(index, count, ("key", "audio_progress", {"index": index, "count": count, "lexeme": lexeme}))

            self._emit_progress(0, total, ("key", "downloading_audio", {}))
            audio, failures = collect_audio(words, self._session, 3, on_progress=on_progress)
            if self._stop:
                return
            self._emit_progress(total, total, ("key", "writing_deck", {}))
            directory = Path(tempfile.mkdtemp(prefix="preply-anki-"))
            path = build_apkg(directory / "Preply Vocabulary.apkg", words, audio, DEFAULT_DECK)
            self.build_ready.emit(str(path), failures)
        except SessionCancelled:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)
        except Exception as exc:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)
            if not self._stop:
                self._emit_failure(self.build_failed, self._build_failed_queue, phrase_for_exception(exc))

    def _close_session(self) -> None:
        """Close the requests client. The sign-in window and Opera stay open."""
        session = self._session
        self._session = None
        if session is None:
            return
        session.close()

    def _close_login_window(self) -> None:
        """Close only the profile window this program opened."""
        window = self._login
        self._login = None
        if window is None:
            return
        try:
            window.close()
        except Exception:
            return

    def _fail_closed_window(self) -> None:
        """The user closed the sign-in window. Keep Opera open and ask for a new one."""
        self._close_session()
        self._close_login_window()
        if not self._stop:
            self._emit_failure(self.connect_failed, self._connect_failed_queue, ("key", "login_window_closed", {}))


class DependencyPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.title = QLabel(tr("deps_title"))
        self.title.setObjectName("title")
        self.hint = QLabel(tr("deps_hint"))
        self.hint.setWordWrap(True)
        self.lines = QLabel("")
        self.lines.setObjectName("detail")
        self.lines.setWordWrap(True)
        self.lines.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.install_button = QPushButton(tr("install_components"))
        self.install_button.setObjectName("primary")
        self.install_button.setEnabled(False)
        self.install_button.hide()
        self._repair = False
        row = QHBoxLayout()
        row.addWidget(self.install_button)
        row.addStretch(1)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addWidget(self.lines)
        layout.addLayout(row)
        layout.addStretch(1)

    def show_report(self, report: dict[str, bool], *, repair: bool) -> None:
        rows = []
        for key in DEPENDENCY_ORDER:
            name = tr(f"component_{key}")
            state_key = "component_ok" if report.get(key) else "component_missing"
            rows.append(tr(state_key, name=name))
        self.lines.setText("\n".join(rows))
        ready = dependencies_ready(report)
        self._repair = repair and not ready
        self.install_button.setVisible(not ready)
        self.install_button.setEnabled(not ready)
        self.install_button.setText(tr("repair_components" if self._repair else "install_components"))

    def retranslate(self) -> None:
        self.title.setText(tr("deps_title"))
        self.hint.setText(tr("deps_hint"))
        self.install_button.setText(tr("repair_components" if self._repair else "install_components"))


class LoginPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.hint = QLabel(tr("login_hint"))
        self.hint.setWordWrap(True)
        self.authorize_button = QPushButton(tr("authorize"))
        self.authorize_button.setObjectName("primary")
        self.continue_button = QPushButton(tr("signed_in"))
        self.continue_button.setObjectName("primary")
        self.continue_button.hide()
        row = QHBoxLayout()
        row.addWidget(self.authorize_button)
        row.addWidget(self.continue_button)
        row.addStretch(1)
        layout.addWidget(self.hint)
        layout.addLayout(row)
        layout.addStretch(1)

    def retranslate(self) -> None:
        self.hint.setText(tr("login_hint"))
        self.authorize_button.setText(tr("authorize"))
        self.continue_button.setText(tr("signed_in"))


class LanguagePanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._aliases: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.title = QLabel(tr("choose_language_title"))
        self.title.setObjectName("title")
        self.hint = QLabel(tr("choose_language_hint"))
        self.hint.setWordWrap(True)
        self.language_list = QListWidget()
        self.language_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.language_list.currentItemChanged.connect(lambda _current, _previous: self._update_confirm())
        self.confirm_button = QPushButton(tr("continue"))
        self.confirm_button.setObjectName("primary")
        self.confirm_button.setEnabled(False)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.confirm_button)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addWidget(self.language_list, 1)
        layout.addLayout(row)

    def set_languages(self, languages: list[tuple[str, str]]) -> None:
        self._aliases = []
        self.language_list.clear()
        for alias, label in languages:
            cleaned = alias.strip().lower()
            if not cleaned:
                continue
            self._aliases.append(cleaned)
            item = QListWidgetItem(label or cleaned)
            item.setData(Qt.ItemDataRole.UserRole, cleaned)
            self.language_list.addItem(item)
        self._update_confirm()

    def selected_alias(self) -> str:
        item = self.language_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _update_confirm(self) -> None:
        self.confirm_button.setEnabled(bool(self.selected_alias()))

    def retranslate(self) -> None:
        self.title.setText(tr("choose_language_title"))
        self.hint.setText(tr("choose_language_hint"))
        self.confirm_button.setText(tr("continue"))


class WordListPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._words: dict[str, VocabWord] = {}
        self._order: list[str] = []
        self._busy = False
        self._subject = ""
        self._can_switch = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        subject_row = QHBoxLayout()
        self.language_caption = QLabel(tr("language_word"))
        subject_row.addWidget(self.language_caption)
        self.language_label = QLabel("—")
        self.switch_button = QPushButton(tr("change_language"))
        self.switch_button.hide()
        subject_row.addWidget(self.language_label)
        subject_row.addStretch(1)
        subject_row.addWidget(self.switch_button)
        layout.addLayout(subject_row)

        self.empty_label = QLabel(tr("no_saved_words"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        self.word_list = QListWidget()
        self.word_list.setAlternatingRowColors(True)
        self.word_list.itemChanged.connect(lambda _item: self._update_confirm())
        layout.addWidget(self.word_list, 1)

        self.count_label = QLabel(tr("selected_count", selected=0, total=0))
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.select_all_button = QPushButton(tr("select_all"))
        self.clear_button = QPushButton(tr("clear_selection"))
        self.refresh_button = QPushButton(tr("refresh"))
        self.next_button = QPushButton(tr("next"))
        self.next_button.setObjectName("primary")
        self.select_all_button.clicked.connect(self.select_all)
        self.clear_button.clicked.connect(self.clear_selection)
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)
        self._update_confirm()

    def selected_subject(self) -> str:
        return self._subject

    def set_language(self, label: str, alias: str, can_switch: bool) -> None:
        self._subject = alias.strip().lower()
        self._can_switch = can_switch
        self.language_label.setText(label or self._subject or "—")
        self.switch_button.setVisible(can_switch)
        self._update_confirm()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_confirm()

    def set_words(self, words: list[VocabWord], keep_checks: bool = True) -> None:
        previous = self.checked_keys() if keep_checks else set()
        self._words = {word_key(word): word for word in words}
        self._order = [word_key(word) for word in words]
        self.word_list.blockSignals(True)
        self.word_list.clear()
        for word in words:
            key = word_key(word)
            item = QListWidgetItem(display_word(word))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(word.definition or word.lexeme)
            item.setCheckState(Qt.CheckState.Checked if key in previous else Qt.CheckState.Unchecked)
            self.word_list.addItem(item)
        self.word_list.blockSignals(False)
        has_words = bool(words)
        self.empty_label.setVisible(not has_words)
        self.word_list.setVisible(has_words)
        self._update_confirm()

    def checked_keys(self) -> set[str]:
        keys: set[str] = set()
        for index in range(self.word_list.count()):
            item = self.word_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                keys.add(str(item.data(Qt.ItemDataRole.UserRole)))
        return keys

    def selected_words(self) -> list[VocabWord]:
        chosen = self.checked_keys()
        return [self._words[key] for key in self._order if key in chosen]

    def select_all(self, _checked: bool = False) -> None:
        self._set_all(Qt.CheckState.Checked)

    def clear_selection(self, _checked: bool = False) -> None:
        self._set_all(Qt.CheckState.Unchecked)

    def _set_all(self, state: Qt.CheckState) -> None:
        self.word_list.blockSignals(True)
        for index in range(self.word_list.count()):
            self.word_list.item(index).setCheckState(state)
        self.word_list.blockSignals(False)
        self._update_confirm()

    def _update_confirm(self) -> None:
        selected = len(self.checked_keys())
        total = self.word_list.count()
        self.count_label.setText(tr("selected_count", selected=selected, total=total))
        self.switch_button.setEnabled(self._can_switch and not self._busy)
        self.refresh_button.setEnabled(not self._busy and bool(self._subject))
        self.select_all_button.setEnabled(total > 0 and not self._busy)
        self.clear_button.setEnabled(total > 0 and not self._busy)
        self.next_button.setEnabled(selected > 0 and not self._busy)

    def retranslate(self) -> None:
        self.language_caption.setText(tr("language_word"))
        self.switch_button.setText(tr("change_language"))
        self.empty_label.setText(tr("no_saved_words"))
        self.select_all_button.setText(tr("select_all"))
        self.clear_button.setText(tr("clear_selection"))
        self.refresh_button.setText(tr("refresh"))
        self.next_button.setText(tr("next"))
        self.word_list.blockSignals(True)
        for index in range(self.word_list.count()):
            item = self.word_list.item(index)
            word = self._words.get(str(item.data(Qt.ItemDataRole.UserRole)))
            if word is not None:
                item.setText(display_word(word))
        self.word_list.blockSignals(False)
        self._update_confirm()


class BuildPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._detail_phrase: tuple = ("key", "build_preparing", {})
        self._summary_phrase: tuple | None = None
        self.title = QLabel(tr("build_title"))
        self.title.setObjectName("title")
        self.detail = QLabel(phrase_text(self._detail_phrase))
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.back_button = QPushButton(tr("back_to_words"))
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.back_button)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addWidget(self.progress)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        layout.addLayout(button_row)
        self.back_button.hide()

    def reset(self, total: int) -> None:
        self._detail_phrase = ("key", "downloading_audio", {})
        self._summary_phrase = None
        self.detail.setText(phrase_text(self._detail_phrase))
        self.summary.setText("")
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.back_button.hide()

    def show_progress(self, done: int, total: int, text: str, phrase: tuple | None = None) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, total))
        self._detail_phrase = phrase if phrase is not None else ("plain", text)
        self.detail.setText(phrase_text(self._detail_phrase))

    def show_error(self, message: str, phrase: tuple | None = None) -> None:
        self._detail_phrase = ("key", "build_failed_short", {})
        self.detail.setText(phrase_text(self._detail_phrase))
        self._summary_phrase = phrase if phrase is not None else ("plain", message)
        self.summary.setText(phrase_text(self._summary_phrase))
        self.back_button.show()

    def retranslate(self) -> None:
        self.title.setText(tr("build_title"))
        self.back_button.setText(tr("back_to_words"))
        self.detail.setText(phrase_text(self._detail_phrase))
        if self._summary_phrase is not None:
            self.summary.setText(phrase_text(self._summary_phrase))


class SavePanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._folder: Path | None = None
        self._default_dir = ""
        self._temp_path: Path | None = None
        self._failures: list[AudioFailure] = []
        self._saved = False
        self._reported_total: int | None = None
        self._saved_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.title = QLabel(tr("save_title"))
        self.title.setObjectName("title")
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.summary)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel(tr("folder"))
        folder_row.addWidget(self.folder_label)
        self.folder_edit = QLineEdit("")
        self.folder_edit.setReadOnly(True)
        folder_row.addWidget(self.folder_edit, 1)
        layout.addLayout(folder_row)

        name_row = QHBoxLayout()
        self.name_label = QLabel(tr("file_name"))
        name_row.addWidget(self.name_label)
        self.name_edit = QLineEdit("Preply Vocabulary")
        self.name_edit.textChanged.connect(lambda _text: self._refresh_preview())
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        self.preview = QLabel("")
        self.preview.setObjectName("detail")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview)

        self.success = QLabel("")
        self.success.setWordWrap(True)
        self.success.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.success)

        buttons = QHBoxLayout()
        self.back_button = QPushButton(tr("back_to_words"))
        self.restart_button = QPushButton(tr("start_again"))
        self.restart_button.setEnabled(False)
        self.restart_button.hide()
        self.save_button = QPushButton(tr("save"))
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(lambda _checked=False: self._save())
        self.save_as_button = QPushButton(tr("save_as"))
        self.save_as_button.clicked.connect(lambda _checked=False: self._save_as())
        self.default_caption = QLabel("")
        self.default_caption.setObjectName("detail")
        self.default_caption.setWordWrap(True)
        self.default_caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons.addWidget(self.back_button)
        buttons.addStretch(1)
        buttons.addWidget(self.restart_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_as_button)
        layout.addStretch(1)
        layout.addWidget(self.default_caption)
        layout.addLayout(buttons)
        self._refresh_preview()

    def set_result(self, temp_path: Path, failures: list[AudioFailure], total: int) -> None:
        self._temp_path = temp_path
        self._failures = list(failures)
        self._reported_total = total
        self._saved = False
        self._saved_path = None
        self.success.setText("")
        self.back_button.setEnabled(True)
        self.name_edit.setEnabled(True)
        self._render_summary()
        self._refresh_preview()

    def saved(self) -> bool:
        return self._saved

    def apply_default_folder(self, stored: str) -> None:
        """Preview the settings folder. Save uses it even after a deck was already saved."""
        self._default_dir = stored.strip() if isinstance(stored, str) else ""
        self._folder = existing_directory(self._default_dir)
        self.folder_edit.setText(str(self._folder) if self._folder is not None else self._default_dir)
        self._refresh_preview()

    def _render_summary(self) -> None:
        if self._reported_total is None:
            return
        total = self._reported_total
        missed = len(self._failures)
        ready = total - missed
        if missed:
            names = ", ".join(item.lexeme for item in self._failures[:8])
            extra = "" if missed <= 8 else tr("and_more", count=missed - 8)
            self.summary.setText(
                tr(
                    "deck_ready_partial",
                    total=total,
                    ready=ready,
                    missed=missed,
                    names=names,
                    extra=extra,
                )
            )
        else:
            self.summary.setText(tr("deck_ready_full", total=total))

    def retranslate(self) -> None:
        self.title.setText(tr("save_title"))
        self.folder_label.setText(tr("folder"))
        self.name_label.setText(tr("file_name"))
        self.back_button.setText(tr("back_to_words"))
        self.restart_button.setText(tr("start_again"))
        self.save_button.setText(tr("save"))
        self.save_as_button.setText(tr("save_as"))
        self._render_summary()
        if self._saved and self._saved_path is not None:
            self.success.setText(tr("deck_saved", path=self._saved_path))
            self.restart_button.setEnabled(True)
            self.restart_button.show()
        self._refresh_preview()

    def clear_finished_run(self) -> None:
        """Forget the finished save in the window. Does not delete the saved file."""
        self._temp_path = None
        self._failures = []
        self._saved = False
        self._saved_path = None
        self._reported_total = None
        self.success.setText("")
        self.summary.setText("")
        self.restart_button.setEnabled(False)
        self.restart_button.hide()
        self.back_button.setEnabled(True)
        self.name_edit.setEnabled(True)
        self._refresh_preview()

    def _render_default_caption(self) -> None:
        if self._default_dir:
            self.default_caption.setText(tr("default_folder_path", path=self._default_dir))
        else:
            self.default_caption.setText(tr("default_folder_unset"))

    def _refresh_preview(self) -> None:
        self._render_default_caption()
        ready = self._temp_path is not None
        self.save_button.setEnabled(ready)
        self.save_as_button.setEnabled(ready)
        folder = self._folder
        if folder is None:
            self.preview.setText(tr("default_folder_missing"))
            return
        try:
            path = join_deck_path(folder, self.name_edit.text())
        except ValueError:
            self.preview.setText(tr("enter_file_name_hint"))
            return
        self.preview.setText(tr("file_will_be_saved", path=path))

    def _require_built_deck(self) -> bool:
        if self._temp_path is None or not self._temp_path.is_file():
            QMessageBox.warning(self, tr("no_file_title"), tr("deck_not_built"))
            return False
        return True

    def _named_destination(self, folder: Path | str) -> Path:
        return join_deck_path(folder, self.name_edit.text())

    def _confirm_replace(self, destination: Path) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("file_exists_title"))
        box.setText(tr("replace_file", path=destination))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        yes_button = box.button(QMessageBox.StandardButton.Yes)
        no_button = box.button(QMessageBox.StandardButton.No)
        if yes_button is not None:
            yes_button.setText(tr("replace"))
        if no_button is not None:
            no_button.setText(tr("cancel"))
        return box.exec() == QMessageBox.StandardButton.Yes

    def _write_copy(self, destination: Path) -> None:
        source = self._temp_path
        if source is None:
            return
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            failure_path = destination.with_suffix(".failures.json")
            failure_path.write_text(
                json.dumps([item.to_dict() for item in self._failures], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, tr("not_saved_title"), tr("could_not_write", error=exc))
            return
        self._saved = True
        self._saved_path = destination
        self.success.setText(tr("deck_saved", path=destination))
        self.back_button.setEnabled(False)
        self.restart_button.setEnabled(True)
        self.restart_button.show()
        self._refresh_preview()

    def _save(self) -> None:
        if not self._require_built_deck():
            return
        folder = existing_directory(self._default_dir)
        if folder is None:
            self.success.setText(tr("default_folder_missing"))
            return
        try:
            destination = self._named_destination(folder)
        except ValueError:
            QMessageBox.warning(self, tr("file_name"), tr("enter_file_name"))
            return
        self._write_copy(destination)

    def _save_as(self) -> None:
        if not self._require_built_deck():
            return
        try:
            self._named_destination(self._default_dir or ".")
        except ValueError:
            QMessageBox.warning(self, tr("file_name"), tr("enter_file_name"))
            return
        start = str(self._folder) if self._folder is not None else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("choose_deck_folder"), start)
        if not chosen:
            return
        try:
            destination = self._named_destination(chosen)
        except ValueError:
            QMessageBox.warning(self, tr("file_name"), tr("enter_file_name"))
            return
        if destination.exists() and not self._confirm_replace(destination):
            return
        self._write_copy(destination)


class _TitleBar(QWidget):
    """Empty header area that drags a frameless window. Buttons handle their own clicks."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._offset: QPoint | None = None
        self.setObjectName("titleBar")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._window.isMaximized():
            self._offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        else:
            self._offset = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._window._toggle_maximized()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, settings_file: Path | None = None) -> None:
        super().__init__()
        self._settings_path = settings_path() if settings_file is None else settings_file
        stored = load_settings(self._settings_path)
        set_language(stored.ui_language)
        self._theme = DEFAULT_THEME
        self._status_state = "checking"
        self._status_phrase: tuple = ("key", "checking_components", {})
        self._install_attempted = False
        self._dependency_report: dict[str, bool] | None = None
        self._has_profile = False
        self._profile_user_id: str | None = None
        self._profile_display_name: str | None = None
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(tr("window_title"))
        self.resize(920, 700)
        self.setMinimumSize(760, 560)
        self._temp_dir: Path | None = None
        self._build_total = 0
        self._languages: list[str] = []
        self._selected_alias = ""
        self._chooser_needed = False

        root = QWidget()
        root.setObjectName("surface")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self.title_bar = _TitleBar(self)
        header = QHBoxLayout(self.title_bar)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.heading = QLabel(tr("window_title"))
        self.heading.setObjectName("title")
        self.heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.settings_button = QPushButton(tr("settings"))
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self._open_settings)
        self.minimize_button = self._chrome_button("windowButton", "–", tr("window_minimize"))
        self.maximize_button = self._chrome_button("windowButton", "□", tr("window_maximize"))
        self.close_button = self._chrome_button("closeButton", "✕", tr("window_close"))
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button.clicked.connect(self._toggle_maximized)
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.heading, 1)
        header.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.minimize_button)
        header.addWidget(self.maximize_button)
        header.addWidget(self.close_button)
        layout.addWidget(self.title_bar)

        self.step_labels = []
        self.steps_layout = QHBoxLayout()
        layout.addLayout(self.steps_layout)
        self._rebuild_steps()

        self.connection = QLabel(tr("state_checking"))
        self.connection.setObjectName("connection")
        self.detail = QLabel(tr("checking_components"))
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        self.profile_label = QLabel("")
        self.profile_label.setObjectName("detail")
        self.profile_label.setWordWrap(True)
        self.profile_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.install_progress = QProgressBar()
        self.install_progress.setRange(0, 0)
        self.install_progress.setTextVisible(False)
        self.install_progress.hide()
        layout.addWidget(self.connection)
        layout.addWidget(self.detail)
        layout.addWidget(self.profile_label)
        layout.addWidget(self.install_progress)

        self.stack = QStackedWidget()
        self.deps_panel = DependencyPanel()
        self.login_panel = LoginPanel()
        self.language_panel = LanguagePanel()
        self.words_panel = WordListPanel()
        self.build_panel = BuildPanel()
        self.save_panel = SavePanel()
        self.stack.addWidget(self.deps_panel)
        self.stack.addWidget(self.login_panel)
        self.stack.addWidget(self.language_panel)
        self.stack.addWidget(self.words_panel)
        self.stack.addWidget(self.build_panel)
        self.stack.addWidget(self.save_panel)
        layout.addWidget(self.stack, 1)

        self.deps_panel.install_button.clicked.connect(lambda _checked=False: self._install_missing())
        self.login_panel.authorize_button.clicked.connect(lambda _checked=False: self._authorize())
        self.login_panel.continue_button.clicked.connect(lambda _checked=False: self._confirm_login())
        self.language_panel.confirm_button.clicked.connect(lambda _checked=False: self._confirm_language())
        self.words_panel.next_button.clicked.connect(lambda _checked=False: self._start_build())
        self.words_panel.refresh_button.clicked.connect(lambda _checked=False: self._refresh())
        self.words_panel.switch_button.clicked.connect(lambda _checked=False: self._show_step(_STEP_LANGUAGE))
        self.build_panel.back_button.clicked.connect(lambda _checked=False: self._show_step(_STEP_WORDS))
        self.save_panel.back_button.clicked.connect(lambda _checked=False: self._back_to_words())
        self.save_panel.restart_button.clicked.connect(lambda _checked=False: self._restart())

        self.worker = ExportWorker()
        self.worker.status_changed.connect(self._on_status)
        self.worker.words_ready.connect(self._on_words)
        self.worker.load_failed.connect(self._on_load_failed)
        self.worker.profile_ready.connect(self._on_profile)
        self.worker.connect_failed.connect(self._on_connect_failed)
        self.worker.login_window_opened.connect(self._on_login_window_opened)
        self.worker.dependencies_checked.connect(self._on_dependencies)
        self.worker.dependency_failed.connect(self._on_dependency_failed)
        self.worker.build_progress.connect(self._on_build_progress)
        self.worker.build_ready.connect(self._on_build_ready)
        self.worker.build_failed.connect(self._on_build_failed)
        self._show_step(_STEP_DEPS)
        self._apply_phrase(self._status_state, self._status_phrase)
        self.words_panel.set_busy(True)
        self._apply_saved_folder()
        self._apply_theme()
        self.worker.start()
        self.worker.check_dependencies()

    def _step_titles(self) -> list[str]:
        titles = [tr("step_components"), tr("step_login")]
        if self._chooser_needed:
            titles.append(tr("language_word"))
        titles.extend([tr("step_words"), tr("step_build"), tr("step_save")])
        return [tr("step_numbered", index=index, title=title) for index, title in enumerate(titles, start=1)]

    def _indicator_index(self, stack_index: int) -> int:
        if self._chooser_needed:
            return stack_index
        if stack_index <= _STEP_LOGIN:
            return stack_index
        return stack_index - 1

    def _rebuild_steps(self) -> None:
        while self.steps_layout.count():
            item = self.steps_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.step_labels = []
        for text in self._step_titles():
            label = QLabel(text)
            label.setObjectName("step")
            self.step_labels.append(label)
            self.steps_layout.addWidget(label)
        self.steps_layout.addStretch(1)

    def _status_visible(self) -> bool:
        return self.stack.currentIndex() <= _STEP_WORDS

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        indicator = self._indicator_index(index)
        for step, label in enumerate(self.step_labels):
            label.setProperty("active", step == indicator)
            label.setProperty("done", step < indicator)
            self._repolish(label)
        visible = self._status_visible()
        self.connection.setVisible(visible)
        self.detail.setVisible(visible)
        self.profile_label.setVisible(bool(self.profile_label.text()))
        self.install_progress.setVisible(visible and self.connection.property("state") == "installing")
        if self.stack.widget(index) is self.save_panel:
            self._apply_saved_folder()

    def _set_connection(self, state: str, detail: str) -> None:
        self.connection.setText(state_title(state))
        self.connection.setProperty("state", state)
        self._repolish(self.connection)
        self.detail.setText(detail)
        self.install_progress.setVisible(self._status_visible() and state == "installing")
        if state == "failed" and self.stack.currentIndex() == _STEP_LOGIN:
            self.login_panel.authorize_button.setEnabled(True)
            if self.login_panel.continue_button.isVisible():
                self.login_panel.continue_button.setEnabled(True)
        if state == "failed" and self.stack.currentIndex() == _STEP_DEPS:
            self.deps_panel.install_button.setEnabled(True)

    def _repolish(self, widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def _ensure_worker(self) -> None:
        if self.worker.isRunning() or self.worker.stopping():
            return
        self.worker.wait(500)
        if not self.worker.isRunning() and not self.worker.stopping():
            self.worker.start()

    def _install_missing(self) -> None:
        self._install_attempted = True
        self.deps_panel.install_button.setEnabled(False)
        try:
            self._show_step(_STEP_DEPS)
            self._apply_phrase("installing", ("key", "installing_missing", {}))
            self._ensure_worker()
            self.worker.install_dependencies()
        except Exception as exc:
            self.deps_panel.install_button.setEnabled(True)
            self.deps_panel.install_button.setText(tr("repair_components"))
            self._apply_phrase("failed", phrase_for_exception(exc))

    def _authorize(self) -> None:
        self.login_panel.authorize_button.setEnabled(False)
        try:
            self._show_step(_STEP_LOGIN)
            self._apply_phrase("logging_in", ("key", "opening_login", {}))
            self._ensure_worker()
            self.worker.open_login_window()
        except Exception as exc:
            self.login_panel.authorize_button.setEnabled(True)
            if self.login_panel.continue_button.isVisible():
                self.login_panel.continue_button.setEnabled(True)
            self._show_step(_STEP_LOGIN)
            self._apply_phrase("failed", phrase_for_exception(exc))

    def _on_login_window_opened(self) -> None:
        self.login_panel.authorize_button.setEnabled(True)
        self.login_panel.continue_button.setVisible(True)
        self.login_panel.continue_button.setEnabled(True)
        self._show_step(_STEP_LOGIN)
        self._apply_phrase("ready", ("key", "login_opened", {}))

    def _confirm_login(self) -> None:
        self.login_panel.continue_button.setEnabled(False)
        try:
            self._show_step(_STEP_LOGIN)
            self._apply_phrase("logging_in", ("key", "reading_profile", {}))
            self._ensure_worker()
            self.worker.use_browser_cookies()
        except Exception as exc:
            self.login_panel.continue_button.setEnabled(True)
            self._apply_phrase("failed", phrase_for_exception(exc))

    def _on_dependencies(self, report: object) -> None:
        items = dict(report) if isinstance(report, dict) else {}
        self._dependency_report = items
        ready = dependencies_ready(items)
        self.deps_panel.show_report(items, repair=self._install_attempted)
        if ready:
            self._install_attempted = False
            if self.stack.currentIndex() == _STEP_DEPS:
                self._show_step(_STEP_LOGIN)
                self._apply_phrase("ready", ("key", "authorize_waiting", {}))
            return
        self._show_step(_STEP_DEPS)
        detail_key = "deps_repair" if self._install_attempted else "deps_missing"
        self._apply_phrase("failed", ("key", detail_key, {}))

    def _on_dependency_failed(self, message: str) -> None:
        phrase = _pop(self.worker._dependency_failed_queue)
        if phrase is None:
            phrase = ("plain", message)
        self._install_attempted = True
        self.deps_panel.install_button.setVisible(True)
        self.deps_panel.install_button.setEnabled(True)
        self.deps_panel.install_button.setText(tr("repair_components"))
        self.deps_panel._repair = True
        self._show_step(_STEP_DEPS)
        self._apply_phrase("failed", phrase)

    def _refresh(self) -> None:
        subject = self._selected_alias
        if not subject:
            return
        self.words_panel.set_busy(True)
        self._apply_phrase("logging_in", ("key", "refreshing_list", {}))
        self.worker.load(subject)

    def _on_status(self, state: str, detail: str) -> None:
        item = _pop(self.worker._status_queue)
        if not self._status_visible():
            return
        if item is None:
            self._apply_phrase(state, ("plain", detail))
            return
        queued_state, phrase = item
        self._apply_phrase(queued_state, phrase)

    def _on_profile(self, profile: object) -> None:
        learner = profile if isinstance(profile, LearnerProfile) else None
        if learner is None:
            self._on_connect_failed_phrase(("key", "profile_unreadable", {}))
            return
        self._has_profile = True
        self._profile_user_id = learner.user_id
        self._profile_display_name = learner.display_name
        self._render_profile()
        self._languages = list(learner.subject_aliases)
        action, _subject = language_plan(self._languages)
        self._chooser_needed = action == "choose"
        self._rebuild_steps()
        self.login_panel.authorize_button.setEnabled(True)
        if self.login_panel.continue_button.isVisible():
            self.login_panel.continue_button.setEnabled(True)
        alias = vocabulary_alias(self._languages)
        if alias:
            self._open_vocabulary(alias)
            return
        if action == "choose":
            self.language_panel.set_languages([(item, language_label(item)) for item in self._languages])
            self.words_panel.set_busy(False)
            self._show_step(_STEP_LANGUAGE)
            self._apply_phrase("connected", ("key", "choose_language_wait", {}))
            return
        self._selected_alias = ""
        self.words_panel.set_busy(False)
        self._show_step(_STEP_LOGIN)
        self._apply_phrase("failed", ("key", "no_studied_languages", {}))

    def _confirm_language(self) -> None:
        try:
            chosen = vocabulary_alias(self._languages, self.language_panel.selected_alias())
        except ValueError:
            chosen = None
        if not chosen:
            self._apply_phrase("failed", ("key", "language_not_in_profile", {}))
            return
        self._open_vocabulary(chosen)

    def _open_vocabulary(self, alias: str) -> None:
        self._selected_alias = alias
        self.words_panel.set_language(language_label(alias), alias, self._chooser_needed)
        self.words_panel.set_busy(True)
        self._show_step(_STEP_WORDS)
        self._apply_phrase("connected", ("key", "loading_vocab", {}))
        self.worker.load(alias)

    def _on_words(self, words: object) -> None:
        loaded = list(words)
        self.words_panel.set_words(loaded, keep_checks=True)
        self.words_panel.set_busy(False)
        if not loaded and self.stack.currentIndex() == _STEP_WORDS:
            self._apply_phrase("connected", ("key", "no_saved_words", {}))

    def _on_connect_failed(self, message: str) -> None:
        phrase = _pop(self.worker._connect_failed_queue)
        if phrase is None:
            phrase = ("plain", message)
        self._on_connect_failed_phrase(phrase)

    def _on_connect_failed_phrase(self, phrase: tuple) -> None:
        self.words_panel.set_busy(False)
        self.login_panel.authorize_button.setEnabled(True)
        if self.login_panel.continue_button.isVisible():
            self.login_panel.continue_button.setEnabled(True)
        self._show_step(_STEP_LOGIN)
        self._apply_phrase("failed", phrase)

    def _on_load_failed(self, message: str) -> None:
        phrase = _pop(self.worker._load_failed_queue)
        if phrase is None:
            phrase = ("plain", message)
        self.words_panel.set_busy(False)
        self._show_step(_STEP_WORDS if self._selected_alias else _STEP_LOGIN)
        self._apply_phrase("failed", phrase)

    def _start_build(self) -> None:
        words = self.words_panel.selected_words()
        if not words:
            return
        self._discard_temp()
        self._build_total = len(words)
        self.build_panel.reset(len(words))
        self._show_step(_STEP_BUILD)
        self.worker.build(words)

    def _on_build_progress(self, done: int, total: int, text: str) -> None:
        self.build_panel.show_progress(done, total, text, _pop(self.worker._progress_queue))

    def _on_build_ready(self, path: str, failures: object) -> None:
        temp_path = Path(path)
        self._temp_dir = temp_path.parent
        failure_list = list(failures)
        self.save_panel.set_result(temp_path, failure_list, self._build_total)
        self._show_step(_STEP_SAVE)

    def _on_build_failed(self, message: str) -> None:
        self._show_step(_STEP_BUILD)
        self.build_panel.show_error(message, _pop(self.worker._build_failed_queue))

    def _back_to_words(self) -> None:
        if self.save_panel.saved():
            return
        self._show_step(_STEP_WORDS)

    def _restart(self) -> None:
        """Return to the dependency check so the export can be run again."""
        if not self.save_panel.saved():
            return
        self._clear_temp_deck()
        self.save_panel.clear_finished_run()
        self.words_panel.set_words([], keep_checks=False)
        self.words_panel.set_language("—", "", False)
        self.words_panel.set_busy(True)
        self.build_panel.reset(0)
        self._build_total = 0
        self._has_profile = False
        self._profile_user_id = None
        self._profile_display_name = None
        self.profile_label.setText("")
        self._languages = []
        self._selected_alias = ""
        self._chooser_needed = False
        self.language_panel.language_list.clear()
        self._rebuild_steps()
        self.login_panel.authorize_button.setEnabled(True)
        self.login_panel.continue_button.hide()
        self._install_attempted = False
        self._show_step(_STEP_DEPS)
        self._apply_phrase("checking", ("key", "checking_components", {}))
        self._ensure_worker()
        self.worker.release_session()
        self.worker.check_dependencies()

    def _clear_temp_deck(self) -> None:
        """Remove the temporary deck. The file the user already saved stays."""
        if self._temp_dir is not None and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None

    def _discard_temp(self) -> None:
        if self.save_panel.saved():
            return
        if self._temp_dir is not None and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None

    def _apply_phrase(self, state: str, phrase: tuple) -> None:
        self._status_state = state
        self._status_phrase = phrase
        self._set_connection(state, phrase_text(phrase))

    def _render_profile(self) -> None:
        if not self._has_profile:
            return
        self.profile_label.setText(profile_text(self._profile_user_id, self._profile_display_name))

    def _apply_saved_folder(self) -> None:
        self.save_panel.apply_default_folder(load_settings(self._settings_path).default_save_dir)

    def _apply_theme(self) -> None:
        """Always the dark navy sheet, including when settings name another theme."""
        self._theme = apply_theme(QApplication.instance(), DEFAULT_THEME)

    def _open_settings(self) -> None:
        dialog = self._make_settings_dialog(load_settings(self._settings_path))
        dialog.exec()

    def _make_settings_dialog(self, current: AppSettings) -> SettingsDialog:
        dialog = SettingsDialog(self, current)

        def on_finished(result: int) -> None:
            if result == QDialog.DialogCode.Accepted:
                self._commit_settings(dialog.chosen())

        dialog.finished.connect(on_finished)
        return dialog

    def _commit_settings(self, settings) -> None:
        save_settings(settings, self._settings_path)
        set_language(settings.ui_language)
        self._apply_theme()
        self.retranslate()
        self._apply_saved_folder()

    def retranslate(self) -> None:
        current = self.stack.currentIndex()
        self.setWindowTitle(tr("window_title"))
        self.heading.setText(tr("window_title"))
        self.settings_button.setText(tr("settings"))
        self.minimize_button.setToolTip(tr("window_minimize"))
        self.close_button.setToolTip(tr("window_close"))
        self._sync_maximize_button()
        self._rebuild_steps()
        self.deps_panel.retranslate()
        if self._dependency_report is not None:
            self.deps_panel.show_report(self._dependency_report, repair=self._install_attempted)
        self.login_panel.retranslate()
        self.language_panel.retranslate()
        self.words_panel.retranslate()
        self.build_panel.retranslate()
        self.save_panel.retranslate()
        self._render_profile()
        self._set_connection(self._status_state, phrase_text(self._status_phrase))
        self._show_step(current)

    def _chrome_button(self, object_name: str, glyph: str, tip: str) -> QPushButton:
        button = QPushButton(glyph)
        button.setObjectName(object_name)
        button.setToolTip(tip)
        button.setFixedSize(36, 32)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        return button

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_maximize_button()

    def _sync_maximize_button(self) -> None:
        if self.isMaximized():
            self.maximize_button.setText("❐")
            self.maximize_button.setToolTip(tr("window_restore"))
        else:
            self.maximize_button.setText("□")
            self.maximize_button.setToolTip(tr("window_maximize"))

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "maximize_button"):
            self._sync_maximize_button()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._discard_temp()
        self.worker.request_stop()
        self.worker.wait(30000)
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setFont(QFont("Segoe UI", 11))
    apply_theme(app, DEFAULT_THEME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
