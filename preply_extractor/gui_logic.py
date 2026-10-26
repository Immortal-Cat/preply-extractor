"""Selection and save-path rules for the desktop wizard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from preply_extractor.words import VocabWord

_INVALID_FILENAME = set('\\/:*?"<>|')


def word_key(word: VocabWord) -> str:
    if word.id:
        return word.id
    return f"{word.lexeme}\n{word.definition}"


def word_label(word: VocabWord, limit: int = 110) -> str:
    definition = word.definition or "без определения"
    if len(definition) > limit:
        definition = definition[: limit - 1].rstrip() + "…"
    return f"{word.lexeme}  —  {definition}"


def join_deck_path(folder: Path | str, filename: str) -> Path:
    """Join a folder and a file name, adding ``.apkg`` when it is missing.

    Raises ``ValueError`` when the name is empty or not a single file name.
    """
    folder_text = str(folder).strip()
    if not folder_text:
        raise ValueError("empty folder")
    raw = filename.strip().strip('"')
    if raw.lower().endswith(".apkg"):
        stem = raw[:-5].strip().rstrip(".")
        if not stem:
            raise ValueError("empty name")
        raw = f"{stem}.apkg"
    else:
        raw = raw.rstrip(" .")
        if not raw or raw in {".", ".."}:
            raise ValueError("empty name")
        raw = f"{raw}.apkg"
    if any(char in raw for char in _INVALID_FILENAME):
        raise ValueError("invalid name")
    return Path(folder_text) / raw


class BrowserInstallError(RuntimeError):
    def __init__(self, returncode: int, output: str) -> None:
        self.returncode = returncode
        self.output = output
        super().__init__(output.strip() or f"код {returncode}")


def is_missing_chromium_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "executable doesn't exist" in lowered
        or "please run the following command" in lowered
        or "chromium for playwright is not installed" in lowered
    )


def console_python(executable: str | None = None) -> str:
    """Console interpreter for the Playwright installer.

    The desktop shortcut starts the window with pythonw.exe. That program has
    no console, and ``pythonw -m playwright install chromium`` can exit without
    output or never return. The installer has to use python.exe beside it.
    """
    path = Path(executable or sys.executable)
    name = path.name.lower()
    if name in {"pythonw.exe", "pythonw"}:
        sibling_name = "python.exe" if name.endswith(".exe") else "python"
        sibling = path.with_name(sibling_name)
        if sibling.is_file():
            return str(sibling)
    return str(path)


def install_chromium_command(executable: str | None = None) -> list[str]:
    return [console_python(executable), "-m", "playwright", "install", "chromium"]


PLAYWRIGHT_PIP_SPEC = "playwright==1.49.1"


def playwright_importable() -> bool:
    """True when this interpreter can import the Playwright package. Does not download it."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def install_playwright_package_command(executable: str | None = None) -> list[str]:
    return [console_python(executable), "-m", "pip", "install", PLAYWRIGHT_PIP_SPEC]


def chromium_executable_exists() -> bool:
    """True when Playwright's Chromium file is already on disk. Does not download it."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).is_file()


def run_chromium_install(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.returncode, (completed.stderr or completed.stdout or "").strip()


def language_label(alias: str) -> str:
    """Human label for a subject alias. The API value stays the lowercase alias."""
    parts = [part for part in alias.replace("_", "-").replace("-", " ").split() if part]
    if not parts:
        return alias
    return " ".join(part[:1].upper() + part[1:] for part in parts)


def format_profile(user_id: str | None, display_name: str | None) -> str:
    """Show the id and name that were actually returned. Missing fields stay blank of invented text."""
    identity = (user_id or "").strip() or "нет в ответе"
    name = (display_name or "").strip() or "нет в ответе"
    return f"Идентификатор: {identity}\nИмя: {name}"


def language_plan(aliases: list[str]) -> tuple[str, str | None]:
    """Return ``(action, subject)``.

    ``none`` and ``choose`` do not name a subject, so vocabulary must not be fetched.
    ``auto`` names the single alias.
    """
    cleaned: list[str] = []
    for alias in aliases:
        text = alias.strip().lower()
        if text and text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        return "none", None
    if len(cleaned) == 1:
        return "auto", cleaned[0]
    return "choose", None


def confirm_language(aliases: list[str], choice: str) -> str:
    """Return the chosen alias only when it is one of the profile languages."""
    cleaned = [alias.strip().lower() for alias in aliases if alias.strip()]
    chosen = choice.strip().lower()
    if chosen not in cleaned:
        raise ValueError("unknown language")
    return chosen


_COMMAND_HINTS = (
    "playwright install",
    "please run the following command",
    "в консоли",
    "выполните",
    "pip install",
    "console",
)


def missing_browser_message(detail: str = "") -> str:
    """Tell the user the browser is missing and that they can retry. No terminal command."""
    reason = _install_failure_detail(detail)
    if reason:
        return f"Браузер для входа не установлен. {reason} Нажмите «Повторить»."
    return "Браузер для входа не установлен. Нажмите «Повторить»."


def _install_failure_detail(detail: str) -> str:
    kept: list[str] = []
    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        essential = (
            "no module named" in lowered
            or "modulenotfounderror" in lowered
            or ".exe:" in lowered
        )
        if not essential and any(hint in lowered for hint in _COMMAND_HINTS):
            continue
        kept.append(line)
    text = "\n".join(kept)
    if len(text) > 1500:
        return text[:1500].rstrip() + "…"
    return text


def vocabulary_alias(aliases: list[str], confirmed: str | None = None) -> str | None:
    """Alias to request, or None when vocabulary must not be loaded yet."""
    action, subject = language_plan(aliases)
    if action == "auto":
        return subject
    if action != "choose" or confirmed is None or not confirmed.strip():
        return None
    return confirm_language(aliases, confirmed)


def ensure_playwright_chromium(*, installed: bool, run=run_chromium_install) -> bool:
    """Install Chromium only when it is missing.

    ``run`` receives the install command and returns ``(exit_code, output)``.
    A zero exit is success. Any other code raises ``BrowserInstallError``.
    Returns True when an install was started.
    """
    if installed:
        return False
    code, output = run(install_chromium_command())
    if code != 0:
        raise BrowserInstallError(code, output)
    return True


def prepare_playwright_runtime(*, package_installed: bool, chromium_installed: bool, run) -> list[list[str]]:
    """Install the Playwright package if import fails, then Chromium if the browser is missing.

    ``run`` receives each command and returns ``(exit_code, output)``.
    A non-zero exit raises ``BrowserInstallError`` with that output.
    Returns the commands that were started.
    """
    started: list[list[str]] = []
    if not package_installed:
        command = install_playwright_package_command()
        started.append(command)
        code, output = run(command)
        if code != 0:
            raise BrowserInstallError(code, output)
    if not chromium_installed:
        command = install_chromium_command()
        started.append(command)
        code, output = run(command)
        if code != 0:
            raise BrowserInstallError(code, output)
    return started
