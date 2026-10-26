"""Offline check: a fixture deck uses the reference note type."""

from __future__ import annotations

import inspect
import json
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import preply_extractor.gui as gui_module
from preply_extractor.anki_package import build_apkg, note_guid
from preply_extractor.model import AFMT, CSS, FIELDS, MODEL_ID, MODEL_NAME, QFMT
from preply_extractor.preply_api import fetch_vocabulary
from preply_extractor.words import FIELD_SEPARATOR, format_audio_field, load_words, word_from_node

REFERENCE_MODEL = ROOT / "reference" / "model.json"
FIXTURE = ROOT / "tests" / "fixtures" / "sample_vocab.json"


def silent_wav() -> bytes:
    data = b"\x00\x00" * 160
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        8000,
        16000,
        2,
        16,
        b"data",
        len(data),
    ) + data


class DeckMatchTests(unittest.TestCase):
    def test_examples_follow_the_vocabulary_page_order(self) -> None:
        word = word_from_node(
            {
                "id": "node-1",
                "termDefinition": {
                    "spelling": "speak",
                    "pronunciationUrl": "https://preply.com/audio/speak.mp3",
                    "examples": [{"id": "t1", "example": "Can I speak?"}],
                    "definition": {
                        "definition": "to say words",
                        "examples": [
                            {"id": "d1", "example": "Would you mind?"},
                            {"id": "d2", "example": "Can I speak?"},
                        ],
                    },
                },
            },
            "ADDED_READY_TO_PRACTICE",
        )
        self.assertIsNotNone(word)
        assert word is not None
        self.assertEqual(word.examples, ["Can I speak?", "Would you mind?"])
        self.assertEqual(
            format_audio_field("vocab_0001_speak.mp3", word.examples),
            "[sound:vocab_0001_speak.mp3]<br><br>1. Can I speak?<br>2. Would you mind?",
        )

    def test_pagination_keeps_every_page(self) -> None:
        pages = {
            None: ["one", "two"],
            "cursor-1": ["three"],
        }

        def post_json(path: str, payload: dict) -> tuple[int, str]:
            after = payload["variables"]["after"]
            spellings = pages[after]
            body = {
                "data": {
                    "vocabularyBank": {
                        "summary": {
                            "pendingCount": 0,
                            "learningReadyToPracticeCount": 3,
                            "learningUpNextCount": 0,
                            "masteredCount": 0,
                        },
                        "currentWords": {
                            "hasNext": after is None,
                            "endCursor": "cursor-1" if after is None else None,
                            "nodes": [
                                {
                                    "id": spelling,
                                    "termDefinition": {
                                        "spelling": spelling,
                                        "pronunciationUrl": None,
                                        "definition": {
                                            "definition": f"definition of {spelling}",
                                            "examples": [{"example": f"Use {spelling}."}],
                                        },
                                        "examples": [],
                                    },
                                }
                                for spelling in spellings
                            ],
                        },
                    }
                }
            }
            return 200, json.dumps(body)

        words = fetch_vocabulary(
            post_json,
            subject="english",
            language_code="en",
            page_size=20,
            delay_seconds=0,
            categories=(("ADDED_READY_TO_PRACTICE", "Ready to practice"),),
        )
        self.assertEqual([word.lexeme for word in words], ["one", "two", "three"])

    def test_generated_package_matches_reference_model(self) -> None:
        reference = json.loads(REFERENCE_MODEL.read_text(encoding="utf-8"))["model"]
        self.assertEqual(reference["name"], MODEL_NAME)
        self.assertEqual(reference["css"], CSS)
        self.assertEqual(reference["tmpls"][0]["qfmt"], QFMT)
        self.assertEqual(reference["tmpls"][0]["afmt"], AFMT)
        self.assertEqual([field["name"] for field in reference["flds"]], list(FIELDS))

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            wav = silent_wav()
            (folder / "speak.wav").write_bytes(wav)
            fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
            for item in fixture["words"]:
                if item.get("audioFile"):
                    item["audioFile"] = str(folder / "speak.wav")
            words_path = folder / "words.json"
            words_path.write_text(json.dumps(fixture), encoding="utf-8")
            output = folder / "Preply Vocabulary.apkg"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "preply_extractor",
                    "--json",
                    str(words_path),
                    "--output",
                    str(output),
                    "--deck-name",
                    "Preply Vocabulary",
                    "--no-save-json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            print(completed.stdout)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.is_file())
            self._assert_package(output, wav, reference)

    def _assert_package(self, package: Path, wav: bytes, reference: dict) -> None:
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertIn("collection.anki2", names)
            self.assertIn("collection.anki21", names)
            self.assertIn("media", names)
            media = json.loads(archive.read("media").decode("utf-8"))
            self.assertEqual(
                media,
                {"0": "vocab_0001_speak.wav", "1": "vocab_0003_English.wav"},
            )
            self.assertEqual(archive.read("0"), wav)
            self.assertEqual(archive.read("1"), wav)
            database = archive.read("collection.anki2")
            self.assertEqual(database, archive.read("collection.anki21"))

        db_path = package.with_suffix(".anki2")
        db_path.write_bytes(database)
        try:
            connection = sqlite3.connect(db_path)
            columns = [row[1] for row in connection.execute("PRAGMA table_info(col)")]
            collection = dict(zip(columns, connection.execute("SELECT * FROM col").fetchone()))
            models = json.loads(collection["models"])
            model = models[str(MODEL_ID)]
            decks = json.loads(collection["decks"])
            config = json.loads(collection["dconf"])["1"]
            notes = connection.execute(
                "SELECT flds, tags, mid, guid FROM notes ORDER BY id"
            ).fetchall()
            cards = connection.execute("SELECT did, ord FROM cards ORDER BY id").fetchall()
        finally:
            connection.close()
            db_path.unlink()

        self.assertEqual(model["name"], reference["name"])
        self.assertEqual(model["css"], reference["css"])
        self.assertEqual(model["sortf"], reference["sortf"])
        self.assertEqual(model["type"], reference["type"])
        self.assertEqual(model["tmpls"][0]["qfmt"], reference["tmpls"][0]["qfmt"])
        self.assertEqual(model["tmpls"][0]["afmt"], reference["tmpls"][0]["afmt"])
        self.assertEqual(model["req"], reference["req"])
        self.assertEqual(
            [(field["name"], field["ord"], field["font"], field["size"]) for field in model["flds"]],
            [(field["name"], field["ord"], field["font"], field["size"]) for field in reference["flds"]],
        )
        self.assertTrue(config["autoplay"])
        self.assertTrue(config["replayq"])
        deck_names = {deck["name"] for deck in decks.values()}
        self.assertIn("Preply Vocabulary", deck_names)
        self.assertEqual(len(notes), 3)
        self.assertEqual(len(cards), 3)
        deck_id = next(deck["id"] for deck in decks.values() if deck["name"] == "Preply Vocabulary")
        self.assertTrue(all(card[0] == deck_id and card[1] == 0 for card in cards))

        expected = [
            (
                "to say words, to use the voice, or to have a conversation with someone",
                "speak",
                "[sound:vocab_0001_speak.wav]<br><br>"
                "1. Would you mind speaking more slowly, please?<br>"
                '2. "Can I speak to Dan please?"',
            ),
            (
                "to know the meaning of something that someone says",
                "understand",
                "1. She explained the whole idea again, but I still didn't understand.",
            ),
            (
                "a language that is spoken in the UK, the US, and in many other countries",
                "English",
                "[sound:vocab_0003_English.wav]",
            ),
        ]
        for note, fields in zip(notes, expected):
            stored = note[0].split("\x1f")
            self.assertEqual(tuple(stored), fields)
            self.assertEqual(note[1], "")
            self.assertEqual(note[2], MODEL_ID)
        self.assertEqual(notes[0][3], note_guid(word_from_node({"id": "fixture-speak", "termDefinition": {"spelling": "speak", "definition": {}}})))


class SubjectNotInDeckTests(unittest.TestCase):
    def test_build_apkg_does_not_store_the_subject(self) -> None:
        subject = "spanish"
        words = load_words(FIXTURE)
        self.assertGreaterEqual(len(words), 1)
        for word in words:
            text = "\n".join([word.id, word.lexeme, word.definition, word.category, *word.examples])
            self.assertNotIn(subject, text.lower())
        audio = {
            word.id: (Path(word.audio_file).suffix, silent_wav())
            for word in words
            if word.audio_file
        }
        self.assertTrue(audio)
        self.assertIs(gui_module.build_apkg, build_apkg)
        self.assertNotIn("subject", inspect.signature(gui_module.build_apkg).parameters)

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            package = gui_module.build_apkg(
                folder / "Preply Vocabulary.apkg",
                words,
                audio,
                gui_module.DEFAULT_DECK,
            )
            deck_names, tags, fields, media_names = _package_surfaces(package)

        self.assertIn(gui_module.DEFAULT_DECK, deck_names)
        self.assertTrue(fields)
        self.assertTrue(any(name.startswith("vocab_") for name in media_names))
        for deck_name in deck_names:
            self.assertNotIn(subject, deck_name.lower())
        for tag in tags:
            self.assertNotIn(subject, tag.lower())
        for field in fields:
            self.assertNotIn(subject, field.lower())
        for name in media_names:
            self.assertNotIn(subject, name.lower())
        self.assertNotIn(subject, package.name.lower())


def _package_surfaces(package: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    with zipfile.ZipFile(package) as archive:
        media = json.loads(archive.read("media").decode("utf-8"))
        media_names = [str(name) for name in media.values()]
        media_names.extend(archive.namelist())
        database = archive.read("collection.anki2")
    db_path = package.with_suffix(".anki2")
    db_path.write_bytes(database)
    try:
        connection = sqlite3.connect(db_path)
        columns = [row[1] for row in connection.execute("PRAGMA table_info(col)")]
        collection = dict(zip(columns, connection.execute("SELECT * FROM col").fetchone()))
        decks = json.loads(collection["decks"])
        deck_names = [str(deck["name"]) for deck in decks.values()]
        note_rows = connection.execute("SELECT tags, flds FROM notes ORDER BY id").fetchall()
        collection_tags = str(collection["tags"])
    finally:
        connection.close()
        db_path.unlink()
    tags = [collection_tags, *[row[0] for row in note_rows]]
    fields = [part for row in note_rows for part in row[1].split(FIELD_SEPARATOR)]
    return deck_names, tags, fields, media_names


if __name__ == "__main__":
    unittest.main(verbosity=2)
