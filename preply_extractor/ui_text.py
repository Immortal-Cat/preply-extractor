"""Russian and English strings for the desktop wizard."""

from __future__ import annotations

import threading

from preply_extractor.gui_logic import (
    BrowserInstallError,
    _install_failure_detail,
    is_missing_chromium_error,
    missing_browser_message,
    word_label,
)
from preply_extractor.preply_api import AuthRequired
from preply_extractor.words import VocabWord

_LOCK = threading.Lock()
_LANGUAGE = "ru"

STRINGS: dict[str, dict[str, str]] = {
    "window_title": {"ru": "Словарь Preply в Anki", "en": "Preply vocabulary to Anki"},
    "settings": {"ru": "Настройки", "en": "Settings"},
    "theme": {"ru": "Оформление", "en": "Theme"},
    "theme_navy": {"ru": "Синий", "en": "Navy"},
    "theme_violet": {"ru": "Фиолетовый", "en": "Violet"},
    "theme_amber": {"ru": "Янтарный", "en": "Amber"},
    "interface_language": {"ru": "Язык интерфейса", "en": "Interface language"},
    "language_name_ru": {"ru": "Русский", "en": "Russian"},
    "language_name_en": {"ru": "Английский", "en": "English"},
    "default_folder": {
        "ru": "Папка по умолчанию для новых версий колоды",
        "en": "Default folder for new deck versions",
    },
    "done": {"ru": "Готово", "en": "OK"},
    "cancel": {"ru": "Отмена", "en": "Cancel"},
    "browse": {"ru": "Обзор…", "en": "Browse…"},
    "choose_deck_folder": {"ru": "Выберите папку для колоды", "en": "Choose a folder for the deck"},
    "step_numbered": {"ru": "{index}. {title}", "en": "{index}. {title}"},
    "step_components": {"ru": "Компоненты", "en": "Components"},
    "step_login": {"ru": "Вход", "en": "Sign in"},
    "language_word": {"ru": "Язык", "en": "Language"},
    "step_words": {"ru": "Слова", "en": "Words"},
    "step_build": {"ru": "Сборка", "en": "Build"},
    "step_save": {"ru": "Сохранение", "en": "Save"},
    "state_connected": {"ru": "Подключено", "en": "Connected"},
    "state_checking": {"ru": "Проверка компонентов…", "en": "Checking components…"},
    "state_ready": {"ru": "Ожидание входа", "en": "Waiting to sign in"},
    "state_logging_in": {"ru": "Вход в Preply…", "en": "Signing in to Preply…"},
    "state_installing": {"ru": "Установка браузера…", "en": "Installing the browser…"},
    "state_failed": {"ru": "Нет подключения", "en": "Not connected"},
    "checking_connection": {"ru": "Проверка подключения…", "en": "Checking the connection…"},
    "checking_components": {"ru": "Проверка компонентов…", "en": "Checking components…"},
    "deps_title": {"ru": "Проверка компонентов", "en": "Component check"},
    "deps_hint": {
        "ru": "Перед входом программа проверяет PySide6, requests, пакет Playwright и браузер Chromium. Окно браузера само не открывается.",
        "en": "Before sign-in the program checks PySide6, requests, the Playwright package, and Chromium. The browser window does not open by itself.",
    },
    "component_ok": {"ru": "{name}: установлено", "en": "{name}: installed"},
    "component_missing": {"ru": "{name}: не установлено", "en": "{name}: missing"},
    "component_pyside6": {"ru": "PySide6", "en": "PySide6"},
    "component_requests": {"ru": "requests", "en": "requests"},
    "component_playwright": {"ru": "Playwright", "en": "Playwright"},
    "component_chromium": {"ru": "Chromium", "en": "Chromium"},
    "install_components": {"ru": "Установить", "en": "Install"},
    "repair_components": {"ru": "Исправить", "en": "Repair"},
    "deps_missing": {
        "ru": "Не хватает компонентов. Нажмите «Установить».",
        "en": "Some components are missing. Click Install.",
    },
    "deps_repair": {
        "ru": "Компоненты всё ещё не готовы. Нажмите «Исправить».",
        "en": "Components are still not ready. Click Repair.",
    },
    "deps_still_missing": {
        "ru": "После установки компоненты всё ещё не готовы. Нажмите «Исправить».",
        "en": "Components are still not ready after installation. Click Repair.",
    },
    "deps_install_failed": {
        "ru": "Не удалось установить компоненты. {reason} Нажмите «Исправить».",
        "en": "Could not install the components. {reason} Click Repair.",
    },
    "deps_install_failed_plain": {
        "ru": "Не удалось установить компоненты. Нажмите «Исправить».",
        "en": "Could not install the components. Click Repair.",
    },
    "installing_named": {"ru": "Установка: {name}…", "en": "Installing: {name}…"},
    "installing_missing": {
        "ru": "Установка недостающих компонентов…",
        "en": "Installing missing components…",
    },
    "authorize": {"ru": "Авторизоваться на Preply", "en": "Authorize on Preply"},
    "authorize_waiting": {
        "ru": "Браузер ещё не открыт. Нажмите «Авторизоваться на Preply». Пароль программа не спрашивает.",
        "en": "The browser is not open yet. Click Authorize on Preply. The program does not ask for your password.",
    },
    "login_opened": {
        "ru": "Страница входа открыта в браузере по умолчанию. Войдите в свой аккаунт и нажмите «Я вошёл». Пароль программа не спрашивает.",
        "en": "The sign-in page is open in your default browser. Sign in to your account and click I've signed in. The program does not ask for your password.",
    },
    "signed_in": {"ru": "Я вошёл", "en": "I've signed in"},
    "login_not_finished": {
        "ru": "В браузере по умолчанию ещё нет входа на preply.com. Завершите вход и нажмите «Я вошёл».",
        "en": "The default browser does not have a preply.com sign-in yet. Finish signing in and click I've signed in.",
    },
    "refreshing_list": {"ru": "Обновление списка…", "en": "Refreshing the list…"},
    "reading_profile": {"ru": "Чтение профиля…", "en": "Reading the profile…"},
    "profile_unreadable": {
        "ru": "Не удалось прочитать профиль. Список слов не загружен.",
        "en": "Could not read the profile. The word list was not loaded.",
    },
    "choose_language_wait": {
        "ru": "Выберите язык. Словарь загрузится после подтверждения.",
        "en": "Choose a language. The word list will load after you confirm.",
    },
    "no_studied_languages": {
        "ru": "В аккаунте нет изучаемых языков. Список слов не загружен.",
        "en": "This account has no languages being studied. The word list was not loaded.",
    },
    "language_not_in_profile": {
        "ru": "Этот язык не найден в профиле. Список слов не загружен.",
        "en": "That language is not in the profile. The word list was not loaded.",
    },
    "loading_vocab": {"ru": "Загрузка словаря…", "en": "Loading the word list…"},
    "subject_missing": {
        "ru": "Язык не выбран. Список слов не загружен.",
        "en": "No language is selected. The word list was not loaded.",
    },
    "words_loaded": {
        "ru": "Загружено слов: {count}. Отметьте те, что попадут в колоду.",
        "en": "Words loaded: {count}. Check the ones to include in the deck.",
    },
    "no_saved_words": {"ru": "Сохранённых слов нет.", "en": "There are no saved words."},
    "opening_browser": {
        "ru": "Открываю браузер. Войдите в свой аккаунт Preply, если вход ещё не выполнен. Пароль программа не спрашивает.",
        "en": "Opening the browser. Sign in to your Preply account if you are not signed in yet. The program does not ask for your password.",
    },
    "installing_browser": {"ru": "Установка браузера…", "en": "Installing the browser…"},
    "installing_browser_detail": {
        "ru": "Браузер для входа не установлен. Установка браузера…",
        "en": "The sign-in browser is not installed. Installing the browser…",
    },
    "category_ready": {"ru": "Готово к практике", "en": "Ready to practice"},
    "category_up_next": {"ru": "Следующие", "en": "Up next"},
    "category_learned": {"ru": "Изученные", "en": "Learned"},
    "category_page": {
        "ru": "{section}: страница {page}, собрано {count}",
        "en": "{section}: page {page}, collected {count}",
    },
    "not_connected": {"ru": "Нет подключения к Preply.", "en": "Not connected to Preply."},
    "audio_progress": {
        "ru": "Аудио {index} из {count}: {lexeme}",
        "en": "Audio {index} of {count}: {lexeme}",
    },
    "downloading_audio": {"ru": "Скачивание произношения…", "en": "Downloading pronunciation…"},
    "writing_deck": {"ru": "Запись файла колоды…", "en": "Writing the deck file…"},
    "login_hint": {
        "ru": "Компоненты уже проверены. Кнопка откроет Preply в вашем обычном браузере, новой вкладкой. Пароль программа не спрашивает и не сохраняет. Когда войдёте, нажмите «Я вошёл».",
        "en": "The components are already checked. The button opens Preply in your usual browser, as a new tab. The program does not ask for your password or store it. After you sign in, click I've signed in.",
    },
    "retry": {"ru": "Повторить", "en": "Retry"},
    "choose_language_title": {"ru": "Выберите язык", "en": "Choose a language"},
    "choose_language_hint": {
        "ru": "В аккаунте несколько языков. Словарь откроется после выбора.",
        "en": "This account has several languages. The word list opens after you choose one.",
    },
    "continue": {"ru": "Продолжить", "en": "Continue"},
    "change_language": {"ru": "Сменить язык", "en": "Change language"},
    "selected_count": {"ru": "Выбрано: {selected} из {total}", "en": "Selected: {selected} of {total}"},
    "select_all": {"ru": "Выделить все", "en": "Select all"},
    "clear_selection": {"ru": "Снять выделение", "en": "Clear selection"},
    "refresh": {"ru": "Обновить", "en": "Refresh"},
    "next": {"ru": "Далее", "en": "Next"},
    "no_definition": {"ru": "без определения", "en": "no definition"},
    "build_title": {"ru": "Сборка колоды", "en": "Build deck"},
    "build_preparing": {"ru": "Подготовка…", "en": "Preparing…"},
    "back_to_words": {"ru": "К списку слов", "en": "Back to word list"},
    "build_failed_short": {"ru": "Не удалось собрать колоду.", "en": "Could not build the deck."},
    "save_title": {"ru": "Сохранение колоды", "en": "Save deck"},
    "folder": {"ru": "Папка", "en": "Folder"},
    "file_name": {"ru": "Имя файла", "en": "File name"},
    "enter_file_name_hint": {
        "ru": "Введите имя файла. Расширение .apkg добавится само.",
        "en": "Enter a file name. The .apkg extension is added automatically.",
    },
    "file_will_be_saved": {"ru": "Файл будет сохранён:\n{path}", "en": "The file will be saved:\n{path}"},
    "no_file_title": {"ru": "Нет файла", "en": "No file"},
    "deck_not_built": {"ru": "Колода ещё не собрана.", "en": "The deck has not been built yet."},
    "enter_file_name": {"ru": "Введите имя файла.", "en": "Enter a file name."},
    "file_exists_title": {"ru": "Файл уже существует", "en": "File already exists"},
    "replace_file": {"ru": "Заменить существующий файл?\n{path}", "en": "Replace the existing file?\n{path}"},
    "replace": {"ru": "Заменить", "en": "Replace"},
    "not_saved_title": {"ru": "Не сохранилось", "en": "Not saved"},
    "could_not_write": {"ru": "Не удалось записать файл.\n{error}", "en": "Could not write the file.\n{error}"},
    "deck_saved": {"ru": "Колода сохранена:\n{path}", "en": "Deck saved:\n{path}"},
    "save": {"ru": "Сохранить", "en": "Save"},
    "deck_ready_partial": {
        "ru": "Колода собрана: {total} слов. Аудио скачано: {ready}. Не скачалось: {missed} ({names}{extra}). Эти слова всё равно в колоде.",
        "en": "Deck is ready: {total} words. Audio downloaded: {ready}. Not downloaded: {missed} ({names}{extra}). Those words are still in the deck.",
    },
    "deck_ready_full": {
        "ru": "Колода собрана: {total} слов. Все файлы произношения на месте.",
        "en": "Deck is ready: {total} words. All pronunciation files are in place.",
    },
    "and_more": {"ru": " и ещё {count}", "en": " and {count} more"},
    "profile_missing": {"ru": "нет в ответе", "en": "not in the response"},
    "profile_lines": {"ru": "Идентификатор: {identity}\nИмя: {name}", "en": "ID: {identity}\nName: {name}"},
    "browser_missing_retry": {
        "ru": "Браузер для входа не установлен. {reason} Нажмите «Повторить».",
        "en": "The sign-in browser is not installed. {reason} Click Retry.",
    },
    "browser_missing_retry_plain": {
        "ru": "Браузер для входа не установлен. Нажмите «Повторить».",
        "en": "The sign-in browser is not installed. Click Retry.",
    },
    "session_inactive": {
        "ru": "Сессия Preply не активна. Войдите в свой аккаунт в окне браузера и нажмите «Обновить».",
        "en": "The Preply session is not active. Sign in to your account in the browser window and click Refresh.",
    },
    "browser_profile": {
        "ru": "Не удалось открыть окно входа. {text}",
        "en": "Could not open the sign-in window. {text}",
    },
    "could_not_reach": {"ru": "Не удалось связаться с Preply.", "en": "Could not reach Preply."},
}

_CATEGORY_KEYS = {
    "Ready to practice": "category_ready",
    "Up next": "category_up_next",
    "Learned": "category_learned",
}

_STATE_KEYS = {
    "connected": "state_connected",
    "checking": "state_checking",
    "ready": "state_ready",
    "logging_in": "state_logging_in",
    "installing": "state_installing",
    "failed": "state_failed",
}


def get_language() -> str:
    with _LOCK:
        return _LANGUAGE


def set_language(language: str) -> str:
    """Switch the process-wide interface language. Unknown values become Russian."""
    global _LANGUAGE
    normalized = language.strip().lower() if isinstance(language, str) else ""
    if normalized not in {"ru", "en"}:
        normalized = "ru"
    with _LOCK:
        _LANGUAGE = normalized
    return normalized


def tr(key: str, language: str | None = None, **kwargs: object) -> str:
    if key == "raw":
        return str(kwargs.get("text", ""))
    lang = language or get_language()
    if lang not in {"ru", "en"}:
        lang = "ru"
    entry = STRINGS.get(key)
    if entry is None:
        template = key
    else:
        template = entry.get(lang) or entry.get("ru") or key
    if key == "category_page":
        label = str(kwargs.get("label", ""))
        section_key = _CATEGORY_KEYS.get(label)
        section = tr(section_key, language=lang) if section_key else label
        kwargs = {**kwargs, "section": section}
    if not kwargs:
        return template
    return template.format(**{name: str(value) for name, value in kwargs.items()})


def state_title(state: str, language: str | None = None) -> str:
    key = _STATE_KEYS.get(state)
    if key is None:
        return state
    return tr(key, language=language)


def profile_text(user_id: str | None, display_name: str | None, language: str | None = None) -> str:
    missing = tr("profile_missing", language=language)
    identity = (user_id or "").strip() or missing
    name = (display_name or "").strip() or missing
    return tr("profile_lines", language=language, identity=identity, name=name)


def display_word(word: VocabWord) -> str:
    if word.definition:
        return word_label(word)
    return f"{word.lexeme}  —  {tr('no_definition')}"


def browser_message(detail: str, language: str | None = None) -> str:
    """Same retry wording as the Russian helper, in the active interface language.

    Russian text comes from ``missing_browser_message`` so the command filter stays in one place.
    """
    lang = language or get_language()
    if lang != "en":
        return missing_browser_message(detail)
    reason = _install_failure_detail(detail)
    if reason:
        return tr("browser_missing_retry", language="en", reason=reason)
    return tr("browser_missing_retry_plain", language="en")


def phrase_for_exception(exc: BaseException) -> tuple:
    """A value ``phrase_text`` can render again after the interface language changes."""
    text = str(exc).strip()
    lowered = text.lower()
    if (
        isinstance(exc, BrowserInstallError)
        or is_missing_chromium_error(text)
        or "playwright install" in lowered
        or "playwright is not installed" in lowered
        or "executable doesn't exist" in lowered
    ):
        return ("browser", text)
    if isinstance(exc, AuthRequired) or "sign" in lowered or "log in" in lowered or "login" in lowered:
        return ("key", "session_inactive", {})
    if "could not open the browser profile" in lowered or "browser profile" in lowered:
        return ("key", "browser_profile", {"text": text})
    if text == "Нет подключения к Preply.":
        return ("key", "not_connected", {})
    if not text:
        return ("key", "could_not_reach", {})
    return ("plain", text)


def phrase_text(phrase: tuple) -> str:
    kind = phrase[0]
    if kind == "browser":
        return browser_message(str(phrase[1]))
    if kind == "key":
        kwargs = phrase[2] if len(phrase) > 2 and isinstance(phrase[2], dict) else {}
        return tr(str(phrase[1]), **kwargs)
    return str(phrase[1])
