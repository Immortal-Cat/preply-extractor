"""Interface language, default deck folder, and theme, stored outside the project tree."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from preply_extractor.themes import DEFAULT_THEME, normalize_theme

_LANGUAGES = {"ru", "en"}


@dataclass(frozen=True)
class AppSettings:
    ui_language: str = "ru"
    default_save_dir: str = ""
    theme: str = DEFAULT_THEME


def settings_path() -> Path:
    """``%APPDATA%\\PreplyExtractor\\settings.json`` on Windows."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "PreplyExtractor" / "settings.json"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "PreplyExtractor" / "settings.json"
    return Path.home() / ".config" / "PreplyExtractor" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings. A missing file, invalid JSON, or an unknown language cannot crash.

    An unknown ``ui_language`` falls back to Russian and no default folder.
    A missing language key stays Russian and still keeps a string folder.
    A missing or unknown ``theme`` becomes navy and keeps the language and folder.
    """
    file = settings_path() if path is None else path
    try:
        raw_text = file.read_text(encoding="utf-8")
    except OSError:
        return AppSettings()
    try:
        raw = json.loads(raw_text)
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    if "ui_language" not in raw:
        language = "ru"
    else:
        value = raw.get("ui_language")
        if not isinstance(value, str) or value.strip().lower() not in _LANGUAGES:
            return AppSettings()
        language = value.strip().lower()
    folder = raw.get("default_save_dir", "")
    if not isinstance(folder, str):
        folder = ""
    return AppSettings(ui_language=language, default_save_dir=folder, theme=normalize_theme(raw.get("theme")))


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    file = settings_path() if path is None else path
    language = settings.ui_language.strip().lower() if isinstance(settings.ui_language, str) else ""
    if language not in _LANGUAGES:
        language = "ru"
    folder = settings.default_save_dir if isinstance(settings.default_save_dir, str) else ""
    payload = {
        "ui_language": language,
        "default_save_dir": folder,
        "theme": normalize_theme(settings.theme),
    }
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def existing_directory(stored: str) -> Path | None:
    """Return ``stored`` when it points at a directory that is still there."""
    if not isinstance(stored, str):
        return None
    text = stored.strip()
    if not text:
        return None
    candidate = Path(text)
    try:
        if candidate.is_dir():
            return candidate
    except OSError:
        return None
    return None
