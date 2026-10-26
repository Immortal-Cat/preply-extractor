"""Load a cookie file the user exported from their own browser."""

from __future__ import annotations

import json
from pathlib import Path


def load_cookie_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"Cookie file is empty: {path}")
    if stripped[0] in "[{":
        return _from_json(json.loads(stripped))
    if stripped.lower().startswith("cookie:"):
        stripped = stripped.split(":", 1)[1].strip()
    lines = []
    for raw in stripped.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        lines.append(line)
    if len(lines) == 1 and "\t" not in lines[0] and "=" in lines[0]:
        return _from_header(lines[0])
    cookies = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies.append(
                {
                    "domain": parts[0],
                    "path": parts[2],
                    "name": parts[5],
                    "value": parts[6],
                }
            )
        elif "=" in line:
            name, value = line.split("=", 1)
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".preply.com",
                    "path": "/",
                }
            )
    if not cookies:
        raise ValueError(
            "Could not read cookies. Use a Netscape cookies.txt, a Cookie-Editor JSON export, "
            "or a single Cookie header line."
        )
    return cookies


def _from_json(data: object) -> list[dict]:
    if isinstance(data, dict):
        if isinstance(data.get("cookies"), list):
            data = data["cookies"]
        else:
            return [
                {"name": str(key), "value": str(value), "domain": ".preply.com", "path": "/"}
                for key, value in data.items()
                if not str(key).startswith("_")
            ]
    if not isinstance(data, list):
        raise ValueError("Cookie JSON must be a list of cookies or a name/value object")
    cookies = []
    for item in data:
        if not isinstance(item, dict) or "name" not in item or "value" not in item:
            continue
        cookies.append(
            {
                "name": str(item["name"]),
                "value": str(item["value"]),
                "domain": str(item.get("domain") or ".preply.com"),
                "path": str(item.get("path") or "/"),
            }
        )
    if not cookies:
        raise ValueError("Cookie JSON did not contain any name/value cookies")
    return cookies


def _from_header(header: str) -> list[dict]:
    cookies = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies.append(
            {"name": name, "value": value.strip(), "domain": ".preply.com", "path": "/"}
        )
    if not cookies:
        raise ValueError("Cookie header did not contain any cookies")
    return cookies
