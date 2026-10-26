"""Build an .apkg whose note type matches the reference deck."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

from preply_extractor.model import DECK_CONFIG, MODEL_ID, note_model
from preply_extractor.words import FIELD_SEPARATOR, VocabWord, format_audio_field, safe_audio_stem

SCHEMA = """
CREATE TABLE col (
    id              integer primary key,
    crt             integer not null,
    mod             integer not null,
    scm             integer not null,
    ver             integer not null,
    dty             integer not null,
    usn             integer not null,
    ls              integer not null,
    conf            text not null,
    models          text not null,
    decks           text not null,
    dconf           text not null,
    tags            text not null
);
CREATE TABLE notes (
    id              integer primary key,
    guid            text not null,
    mid             integer not null,
    mod             integer not null,
    usn             integer not null,
    tags            text not null,
    flds            text not null,
    sfld            text not null,
    csum            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE cards (
    id              integer primary key,
    nid             integer not null,
    did             integer not null,
    ord             integer not null,
    mod             integer not null,
    usn             integer not null,
    type            integer not null,
    queue           integer not null,
    due             integer not null,
    ivl             integer not null,
    factor          integer not null,
    reps            integer not null,
    lapses          integer not null,
    left            integer not null,
    odue            integer not null,
    odid            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE revlog (
    id              integer primary key,
    cid             integer not null,
    usn             integer not null,
    ease            integer not null,
    ivl             integer not null,
    lastIvl         integer not null,
    factor          integer not null,
    time            integer not null,
    type            integer not null
);
CREATE TABLE graves (
    usn             integer not null,
    oid             integer not null,
    type            integer not null
);
CREATE INDEX ix_notes_usn on notes (usn);
CREATE INDEX ix_cards_usn on cards (usn);
CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid);
CREATE INDEX ix_cards_sched on cards (did, queue, due);
CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
"""


def deck_id_for(name: str) -> int:
    digest = hashlib.sha1(f"preply-deck:{name}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
    if value < 10_000_000_000:
        value += 10_000_000_000
    return value


def note_guid(word: VocabWord) -> str:
    identity = word.id or f"{word.lexeme}\n{word.definition}"
    return hashlib.sha1(f"preply-vocab:{identity}".encode("utf-8")).hexdigest()[:16]


def field_checksum(front: str) -> int:
    return int(hashlib.sha1(front.encode("utf-8")).hexdigest()[:8], 16)


def allocate_output(path: Path) -> Path:
    """Never overwrite an existing package."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    suffix = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{stamp}-{suffix}{path.suffix}")
        suffix += 1
    return candidate


def build_apkg(
    path: Path,
    words: list[VocabWord],
    audio_by_id: dict[str, tuple[str, bytes]],
    deck_name: str,
) -> Path:
    """Write a new .apkg. ``audio_by_id`` maps word id -> (extension, bytes)."""
    destination = allocate_output(path)
    deck_id = deck_id_for(deck_name)
    media_map: dict[str, str] = {}
    media_blobs: list[tuple[str, bytes]] = []
    packaged: list[tuple[VocabWord, str, str, str]] = []

    for index, word in enumerate(words, start=1):
        front = word.definition or "(no definition)"
        back = word.lexeme
        filename = None
        blob = audio_by_id.get(word.id)
        if blob is not None:
            extension, data = blob
            filename = f"vocab_{index:04d}_{safe_audio_stem(word.lexeme)}{extension}"
            key = str(len(media_map))
            media_map[key] = filename
            media_blobs.append((key, data))
        audio_field = format_audio_field(filename, word.examples)
        packaged.append((word, front, back, audio_field))

    db_path = destination.with_suffix(".anki2.tmp")
    try:
        _write_collection(db_path, deck_id, deck_name, packaged)
        temp_zip = destination.with_suffix(".apkg.part")
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.write(db_path, "collection.anki2")
            archive.write(db_path, "collection.anki21")
            archive.writestr(
                "media",
                json.dumps(media_map, ensure_ascii=True, separators=(",", ":")),
            )
            for key, data in media_blobs:
                archive.writestr(key, data)
        temp_zip.replace(destination)
    finally:
        if db_path.exists():
            db_path.unlink()
        part = destination.with_suffix(".apkg.part")
        if part.exists():
            part.unlink()
    return destination


def _write_collection(
    db_path: Path,
    deck_id: int,
    deck_name: str,
    packaged: list[tuple[VocabWord, str, str, str]],
) -> None:
    if db_path.exists():
        db_path.unlink()
    now = int(time.time())
    day_start = now - (now % 86400)
    note_base = int(time.time() * 1000)
    model = note_model(deck_id)
    models = {str(MODEL_ID): model}
    decks = {
        "1": _deck(1, "Default", now),
        str(deck_id): _deck(deck_id, deck_name, now),
    }
    conf = {
        "activeDecks": [deck_id],
        "addToCur": True,
        "collapseTime": 1200,
        "curDeck": deck_id,
        "curModel": str(MODEL_ID),
        "dueCounts": True,
        "estTimes": True,
        "newBury": True,
        "newSpread": 0,
        "nextPos": len(packaged) + 1,
        "sortBackwards": False,
        "sortType": "noteFld",
        "timeLim": 0,
    }
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO col VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                day_start,
                now,
                now * 1000,
                11,
                0,
                0,
                0,
                _json(conf),
                _json(models),
                _json(decks),
                _json({"1": DECK_CONFIG}),
                "{}",
            ),
        )
        for offset, (word, front, back, audio_field) in enumerate(packaged):
            fields = FIELD_SEPARATOR.join((front, back, audio_field))
            note_id = note_base + offset
            connection.execute(
                "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    note_id,
                    note_guid(word),
                    MODEL_ID,
                    now,
                    -1,
                    "",
                    fields,
                    front,
                    field_checksum(front),
                    0,
                    "",
                ),
            )
            connection.execute(
                "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    note_base + len(packaged) + offset,
                    note_id,
                    deck_id,
                    0,
                    now,
                    -1,
                    0,
                    0,
                    offset + 1,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    "",
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _deck(deck_id: int, name: str, modified: int) -> dict:
    return {
        "collapsed": False,
        "conf": 1,
        "desc": "",
        "dyn": 0,
        "extendNew": 10,
        "extendRev": 50,
        "id": deck_id,
        "lrnToday": [0, 0],
        "mod": modified,
        "name": name,
        "newToday": [0, 0],
        "revToday": [0, 0],
        "timeToday": [0, 0],
        "usn": -1 if deck_id != 1 else 0,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
