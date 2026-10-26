"""Download pronunciation files for the signed-in user's own words."""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

from preply_extractor.words import VocabWord

MAX_AUDIO_BYTES = 8_000_000
ORIGIN = "https://preply.com"


class AudioFailure:
    def __init__(self, word: VocabWord, reason: str) -> None:
        self.word_id = word.id
        self.lexeme = word.lexeme
        self.url = word.pronunciation_url or word.audio_file or ""
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "id": self.word_id,
            "lexeme": self.lexeme,
            "url": self.url,
            "reason": self.reason,
        }


def extension_for(data: bytes, content_type: str, url: str) -> str | None:
    if not data or len(data) > MAX_AUDIO_BYTES:
        return None
    head = data[:64].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or head.startswith(b"{") or head.startswith(b"["):
        return None
    if data.startswith(b"RIFF") and b"WAVE" in data[:16]:
        return ".wav"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"ID3") or (len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return ".mp3"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ".m4a"
    ctype = content_type.split(";", 1)[0].strip().lower()
    if ctype in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return ".wav"
    if ctype in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if ctype in {"audio/ogg", "audio/opus"}:
        return ".ogg"
    if ctype in {"audio/mp4", "audio/m4a", "audio/aac", "audio/x-m4a"}:
        return ".m4a"
    if ctype.startswith("audio/"):
        path_ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if path_ext in {".wav", ".mp3", ".ogg", ".m4a", ".flac"}:
            return path_ext
        return ".mp3"
    return None


def resolve_audio_url(url: str) -> str:
    return urllib.parse.urljoin(ORIGIN + "/", url)


def read_local_audio(word: VocabWord) -> tuple[bytes, str] | AudioFailure:
    if not word.audio_file:
        return AudioFailure(word, "no audio file")
    path = Path(word.audio_file)
    if not path.is_file():
        return AudioFailure(word, f"audio file not found: {path}")
    data = path.read_bytes()
    ext = extension_for(data, "", path.name) or path.suffix.lower()
    if ext not in {".wav", ".mp3", ".ogg", ".m4a", ".flac"}:
        return AudioFailure(word, f"unsupported local audio type: {ext or 'unknown'}")
    return data, ext


def download_audio(get_bytes, word: VocabWord, retries: int) -> tuple[bytes, str] | AudioFailure:
    """get_bytes(url) -> (status, body, content_type)."""
    if not word.pronunciation_url:
        return AudioFailure(word, "no pronunciation URL")
    url = resolve_audio_url(word.pronunciation_url)
    if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
        return AudioFailure(word, "pronunciation URL is not http(s)")
    last_reason = "download failed"
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            status, body, content_type = get_bytes(url)
        except Exception as exc:  # network error on the user's own file
            last_reason = f"request error: {exc}"
            status, body, content_type = 0, b"", ""
        if status == 200 and body:
            ext = extension_for(body, content_type or "", url)
            if ext:
                return body, ext
            last_reason = f"response was not an audio file (HTTP {status}, {content_type or 'no type'})"
        elif status in {401, 403}:
            return AudioFailure(word, f"HTTP {status} while downloading audio")
        else:
            last_reason = f"HTTP {status}" if status else last_reason
        if attempt < attempts and status not in {401, 403}:
            time.sleep(min(4, attempt))
    return AudioFailure(word, last_reason)
