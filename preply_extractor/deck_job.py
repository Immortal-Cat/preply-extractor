"""Shared export steps used by the command line and the desktop window."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from preply_extractor.audio import AudioFailure, download_audio, read_local_audio
from preply_extractor.preply_api import BrowserSession, public_get_bytes
from preply_extractor.words import VocabWord

ProgressFn = Callable[[int, int, str], None]


class LiveSession:
    """Browser session that stays open until ``close``."""

    def __init__(self, browser: BrowserSession) -> None:
        self._browser = browser
        self._closed = False

    def post_json(self, path: str, payload: dict) -> tuple[int, str]:
        return self._browser.post_json(path, payload)

    @property
    def endpoint(self) -> str:
        return self._browser._endpoint

    def get_bytes(self, url: str) -> tuple[int, bytes, str]:
        return self._browser.get_bytes(url)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._browser.__exit__(None, None, None)
        except Exception:
            return


def open_browser_session(
    user_data_dir: Path | str,
    language_code: str,
    subject: str,
    *,
    headless: bool = False,
    login_timeout: float = 600,
    should_stop: Callable[[], bool] | None = None,
) -> LiveSession:
    browser = BrowserSession(
        str(user_data_dir),
        language_code,
        subject,
        headless,
        login_timeout,
        should_stop,
    )
    browser.__enter__()
    return LiveSession(browser)


def collect_audio(
    words: list[VocabWord],
    getter,
    retries: int,
    on_progress: ProgressFn | None = None,
) -> tuple[dict[str, tuple[str, bytes]], list[AudioFailure]]:
    """Download pronunciation for these words. One failed file does not stop the rest."""
    audio: dict[str, tuple[str, bytes]] = {}
    failures: list[AudioFailure] = []
    get_bytes = getattr(getter, "get_bytes", None) if getter is not None else None
    total = len(words)
    for index, word in enumerate(words, start=1):
        if not word.id:
            word.id = f"{word.lexeme}-{index}"
        result = _one_audio(word, get_bytes, retries)
        if isinstance(result, AudioFailure):
            if word.pronunciation_url or word.audio_file:
                failures.append(result)
        else:
            data, extension = result
            audio[word.id] = (extension, data)
        if on_progress is not None:
            on_progress(index, total, word.lexeme)
        elif index % 25 == 0 or index == total:
            print(f"Audio {index}/{total}")
    return audio, failures


def _one_audio(word: VocabWord, get_bytes, retries: int):
    if word.audio_file:
        return read_local_audio(word)
    if not word.pronunciation_url:
        return AudioFailure(word, "no pronunciation URL")
    downloader = get_bytes or public_get_bytes
    result = download_audio(downloader, word, retries)
    if isinstance(result, AudioFailure) and get_bytes is not None:
        public = download_audio(public_get_bytes, word, 1)
        if not isinstance(public, AudioFailure):
            return public
    return result
