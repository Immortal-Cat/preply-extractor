"""Map a Preply vocabulary payload onto deck fields."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

# The vocabulary page shows term-level examples first, then the definition's
# examples, and drops duplicates while keeping the first occurrence.
FIELD_SEPARATOR = "\x1f"


@dataclass
class VocabWord:
    id: str
    lexeme: str
    definition: str
    examples: list[str]
    pronunciation_url: str | None = None
    category: str = ""
    audio_file: str | None = None

    def to_export(self) -> dict:
        payload = asdict(self)
        payload["pronunciationUrl"] = payload.pop("pronunciation_url")
        payload["audioFile"] = payload.pop("audio_file")
        return payload


def clean_text(value: str) -> str:
    text = value.replace("\x1f", " ").replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(text.split())


def collect_examples(term: dict) -> list[str]:
    definition = term.get("definition") or {}
    buckets = [term.get("examples") or [], definition.get("examples") or []]
    ordered: list[str] = []
    for bucket in buckets:
        for item in bucket:
            if isinstance(item, str):
                raw = item
            elif isinstance(item, dict):
                raw = item.get("example") or ""
            else:
                raw = ""
            text = clean_text(str(raw))
            if text and text not in ordered:
                ordered.append(text)
    return ordered


def word_from_node(node: dict, category: str = "") -> VocabWord | None:
    term = node.get("termDefinition") or {}
    definition = term.get("definition") or {}
    lexeme = clean_text(str(term.get("spelling") or node.get("lexeme") or ""))
    if not lexeme:
        return None
    meaning = clean_text(str(definition.get("definition") or node.get("definition") or ""))
    audio = term.get("pronunciationUrl") or node.get("pronunciationUrl") or node.get("pronunciation_url")
    audio_url = str(audio).strip() if audio else None
    return VocabWord(
        id=str(node.get("id") or ""),
        lexeme=lexeme,
        definition=meaning,
        examples=collect_examples(term) if term else _string_examples(node.get("examples")),
        pronunciation_url=audio_url or None,
        category=str(node.get("category") or category or ""),
        audio_file=_optional_str(node.get("audioFile") or node.get("audio_file")),
    )


def word_from_export_item(item: dict) -> VocabWord | None:
    if "termDefinition" in item:
        return word_from_node(item)
    lexeme = clean_text(
        str(item.get("lexeme") or item.get("expression") or item.get("spelling") or "")
    )
    if not lexeme:
        return None
    definition = clean_text(
        str(item.get("definition") or item.get("definitionEN") or item.get("meaning") or "")
    )
    return VocabWord(
        id=str(item.get("id") or item.get("preply", {}).get("practiceTermId") or ""),
        lexeme=lexeme,
        definition=definition,
        examples=_string_examples(item.get("examples")),
        pronunciation_url=_optional_str(
            item.get("pronunciationUrl") or item.get("pronunciation_url")
        ),
        category=str(item.get("category") or item.get("preply", {}).get("categoryCode") or ""),
        audio_file=_optional_str(item.get("audioFile") or item.get("audio_file")),
    )


def load_words(path: Path) -> list[VocabWord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = _items(data)
    words: list[VocabWord] = []
    base = path.parent
    for item in items:
        if not isinstance(item, dict):
            continue
        word = word_from_export_item(item)
        if word is None:
            continue
        if word.audio_file and not Path(word.audio_file).is_absolute():
            word.audio_file = str((base / word.audio_file).resolve())
        words.append(word)
    return words


def load_har(path: Path) -> list[VocabWord]:
    """Read vocabulary nodes out of a HAR the user captured in their own browser."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = (data.get("log") or {}).get("entries") or []
    found: dict[str, VocabWord] = {}
    order: list[str] = []
    for entry in entries:
        content = ((entry.get("response") or {}).get("content") or {})
        text = content.get("text") or ""
        if "termDefinition" not in text and "vocabularyBank" not in text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        for node in iter_vocab_nodes(payload):
            word = word_from_node(node, str(node.get("category") or ""))
            if word is None:
                continue
            key = word.id or f"{word.lexeme}\n{word.definition}"
            if key in found:
                continue
            found[key] = word
            order.append(key)
    return [found[key] for key in order]


def iter_vocab_nodes(payload: dict):
    bank = ((payload.get("data") or {}).get("vocabularyBank") or {})
    if not isinstance(bank, dict):
        return
    for value in bank.values():
        if not isinstance(value, dict):
            continue
        nodes = value.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict) and "termDefinition" in node:
                    yield node


def format_audio_field(filename: str | None, examples: list[str]) -> str:
    lines = [f"{index}. {example}" for index, example in enumerate(examples, start=1)]
    body = "<br>".join(lines)
    if filename and body:
        return f"[sound:{filename}]<br><br>{body}"
    if filename:
        return f"[sound:{filename}]"
    return body


def safe_audio_stem(lexeme: str) -> str:
    cleaned = lexeme.strip().replace(" ", "_")
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return (cleaned or "word")[:60]


def _items(data: object) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("words", "nodes"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("JSON must be a list of words or an object with a words array")


def _string_examples(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    ordered: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = clean_text(item)
        elif isinstance(item, dict):
            text = clean_text(str(item.get("example") or ""))
        else:
            text = ""
        if text and text not in ordered:
            ordered.append(text)
    return ordered


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
