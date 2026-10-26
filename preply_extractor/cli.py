"""Command line for exporting the signed-in user's Preply vocabulary."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from preply_extractor.anki_package import build_apkg
from preply_extractor.audio import AudioFailure
from preply_extractor.cookies import load_cookie_file
from preply_extractor.deck_job import collect_audio, open_browser_session
from preply_extractor.preply_api import (
    AuthRequired,
    PreplyError,
    RequestsSession,
    fetch_vocabulary,
)
from preply_extractor.words import VocabWord, load_har, load_words

DEFAULT_DECK = "Preply Vocabulary"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (AuthRequired, PreplyError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export your own Preply vocabulary to an Anki deck matching the EN-definition sample."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "Preply Vocabulary.apkg",
        help="Destination .apkg. If it already exists, a new timestamped file is written.",
    )
    parser.add_argument("--deck-name", default=DEFAULT_DECK, help="Anki deck name.")
    parser.add_argument("--subject", default="english", help="Preply subject alias, for example english.")
    parser.add_argument(
        "--ui-language",
        default="en",
        help="Interface language code sent with the vocabulary query. Definitions stay in the subject language.",
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        default=Path(".preply-profile"),
        help="Persistent browser profile. You log in yourself in the window this opens.",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        help="Netscape cookies.txt, Cookie-Editor JSON, or a Cookie header exported from your own browser.",
    )
    parser.add_argument("--json", type=Path, help="Offline vocabulary JSON (a previous export or a fixture).")
    parser.add_argument("--har", type=Path, help="HAR captured from your own Vocabulary page.")
    parser.add_argument("--page-size", type=int, default=20, help="Words per vocabulary page (1-100).")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds to wait between vocabulary pages.")
    parser.add_argument("--audio-retries", type=int, default=3, help="Attempts per pronunciation file.")
    parser.add_argument("--login-timeout", type=float, default=600, help="Seconds to wait while you log in.")
    parser.add_argument("--headless", action="store_true", help="Reuse a profile that is already signed in.")
    parser.add_argument("--skip-audio", action="store_true", help="Build the deck without downloading audio.")
    parser.add_argument("--no-save-json", action="store_true", help="Do not write a vocabulary JSON next to the deck.")
    return parser


def run(args: argparse.Namespace) -> int:
    page_size = min(100, max(1, args.page_size))
    getter = None
    try:
        words, getter = _load_words(args, page_size)
        if not words:
            raise PreplyError("No vocabulary words were found for this subject.")
        audio: dict[str, tuple[str, bytes]] = {}
        failures: list[AudioFailure] = []
        if not args.skip_audio:
            audio, failures = collect_audio(words, getter, args.audio_retries)
    finally:
        if getter is not None and hasattr(getter, "close"):
            getter.close()
    for index, word in enumerate(words, start=1):
        if not word.id:
            word.id = f"{word.lexeme}-{index}"
    destination = build_apkg(args.output, words, audio, args.deck_name)
    if not args.no_save_json:
        _write_export(destination.with_suffix(".vocab.json"), words, args.subject)
    failure_path = destination.with_suffix(".failures.json")
    failure_path.write_text(
        json.dumps([item.to_dict() for item in failures], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {destination}")
    print(f"Notes: {len(words)}")
    print(f"Audio files: {len(audio)}")
    print(f"Audio failures: {len(failures)}")
    if failures:
        print(f"Failure list: {failure_path}")
        for item in failures[:20]:
            print(f"  {item.lexeme}: {item.reason}")
        if len(failures) > 20:
            print(f"  ... {len(failures) - 20} more")
    return 0


def _load_words(args: argparse.Namespace, page_size: int):
    if args.json:
        words = load_words(args.json)
        return words, _audio_getter(args, page_size, required=False)
    if args.har:
        words = load_har(args.har)
        print(f"HAR entries contained {len(words)} words. A partial capture only contains the pages you loaded.")
        return words, _audio_getter(args, page_size, required=False)
    getter = _audio_getter(args, page_size, required=True)
    assert getter is not None
    words = fetch_vocabulary(
        getter.post_json,
        subject=args.subject.strip().lower(),
        language_code=args.ui_language.strip().lower(),
        page_size=page_size,
        delay_seconds=max(0.0, args.delay),
    )
    return words, getter


def _audio_getter(args: argparse.Namespace, page_size: int, required: bool):
    del page_size
    if args.cookies:
        cookies = load_cookie_file(args.cookies)
        return RequestsSession(cookies, args.ui_language.strip().lower() or "en")
    if args.json or args.har:
        if not required:
            return None
    if args.json or args.har:
        return None
    return open_browser_session(
        args.user_data_dir.resolve(),
        args.ui_language.strip().lower() or "en",
        args.subject.strip().lower() or "english",
        headless=args.headless,
        login_timeout=args.login_timeout,
    )


def _write_export(path: Path, words: list[VocabWord], subject: str) -> None:
    payload = {
        "format": "preply-vocabulary-export",
        "version": 1,
        "subjectAlias": subject,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "note": "Personal vocabulary export. Definitions are the ones saved on your account.",
        "words": [word.to_export() for word in words],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
