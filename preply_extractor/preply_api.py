"""Read the signed-in user's Preply vocabulary bank.

This client only calls the vocabulary query the Vocabulary page already uses.
The user logs in themselves. The tool never asks for or stores a password.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from preply_extractor.words import VocabWord, word_from_node

GRAPHQL_PATHS = (
    "/graphql/v2/VocabBankOverview",
    "/graphql/v2",
    "/graphql",
)
ORIGIN = "https://preply.com"

# Same document the vocabulary page sends, including pronunciationUrl.
VOCAB_QUERY = """
query VocabBankOverview(
    $category: VocabBankCategory!
    $first: Int!
    $after: ID
    $languageCode: String!
    $subjectAlias: String
) {
    vocabularyBank {
        summary(subjectAlias: $subjectAlias) {
            pendingCount
            learningReadyToPracticeCount
            learningUpNextCount
            masteredCount
        }
        currentWords: vocabularyBank(
            category: $category
            first: $first
            after: $after
            sortBy: NEXT_PRACTICE_AT
            subjectAlias: $subjectAlias
        ) {
            hasNext
            endCursor
            nodes {
                id
                createdAt
                practicedTimes
                spacedRepetitionProgress
                spacedRepetitionTimeCategory
                source
                sourceLabel
                termDefinition {
                    id
                    spelling
                    pronunciationUrl
                    translation(languageCode: $languageCode) {
                        id
                        translation
                    }
                    definition {
                        id
                        definition
                        cefrLevel
                        translation(languageCode: $languageCode) {
                            id
                            translation
                        }
                        examples {
                            id
                            example
                        }
                        lemma {
                            id
                            typeLabel
                            typeLabelShort
                        }
                    }
                    examples {
                        id
                        example
                    }
                }
            }
        }
    }
}
"""

# Same HeaderUser document the vocabulary site header loads for the signed-in learner.
# fullName is the display name. This operation has no separate username field.
PROFILE_QUERY = """
query HeaderUser {
    currentUser {
        id
        firstName
        fullName
        client {
            id
            subjects {
                subject {
                    id
                    alias
                }
            }
        }
    }
}
"""

# Sections shown on the user's Vocabulary page. PENDING is not saved vocabulary.
CATEGORIES = (
    ("ADDED_READY_TO_PRACTICE", "Ready to practice"),
    ("ADDED_UP_NEXT", "Up next"),
    ("ADDED_MASTERED", "Learned"),
)

GetBytes = Callable[[str], tuple[int, bytes, str]]
PostJson = Callable[[str, dict], tuple[int, str]]


class PreplyError(RuntimeError):
    pass


class AuthRequired(PreplyError):
    pass


class SessionCancelled(PreplyError):
    """The desktop window asked the login wait to stop."""


@dataclass(frozen=True)
class LearnerProfile:
    """Signed-in learner fields read from HeaderUser. Missing strings stay empty."""

    user_id: str
    full_name: str | None
    first_name: str | None
    subject_aliases: tuple[str, ...]

    @property
    def display_name(self) -> str | None:
        if self.full_name:
            return self.full_name
        return self.first_name


def fetch_vocabulary(
    post_json: PostJson,
    *,
    subject: str,
    language_code: str,
    page_size: int,
    delay_seconds: float,
    categories: tuple[tuple[str, str], ...] = CATEGORIES,
    on_page: Callable[[str, int, int], None] | None = None,
) -> list[VocabWord]:
    endpoint = _pick_endpoint(post_json, subject, language_code, categories[0][0], page_size)
    found: dict[str, VocabWord] = {}
    order: list[str] = []
    for code, label in categories:
        nodes = _paginate(
            post_json,
            endpoint,
            category=code,
            label=label,
            subject=subject,
            language_code=language_code,
            page_size=page_size,
            delay_seconds=delay_seconds,
            on_page=on_page,
        )
        added = 0
        for node in nodes:
            word = word_from_node(node, code)
            if word is None:
                continue
            key = word.id or f"{word.lexeme}\n{word.definition}"
            if key in found:
                continue
            if not word.id:
                word.id = key
            found[key] = word
            order.append(key)
            added += 1
        print(f"{label}: {added} words")
    return [found[key] for key in order]


def probe_payload(subject: str, language_code: str, category: str, page_size: int) -> dict:
    return _payload(category, page_size, None, language_code, subject)


def response_status(status: int, body: str) -> str:
    """Return ok, auth, or error."""
    if status in {401, 403}:
        return "auth"
    if status >= 400:
        return "error"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "auth" if _looks_like_html(body) else "error"
    bank = (payload.get("data") or {}).get("vocabularyBank")
    if isinstance(bank, dict) and ("currentWords" in bank or "summary" in bank):
        return "ok"
    messages = " ".join(
        str(item.get("message") or "") for item in (payload.get("errors") or []) if isinstance(item, dict)
    )
    lowered = messages.lower()
    if any(hint in lowered for hint in ("unauth", "not authenticated", "log in", "login", "forbidden", "jwt", "credential")):
        return "auth"
    if messages:
        raise PreplyError(messages)
    if _looks_like_html(body):
        return "auth"
    return "error"


def _paginate(
    post_json: PostJson,
    endpoint: str,
    *,
    category: str,
    label: str,
    subject: str,
    language_code: str,
    page_size: int,
    delay_seconds: float,
    on_page: Callable[[str, int, int], None] | None = None,
) -> list[dict]:
    after = None
    seen: set[str] = set()
    nodes: list[dict] = []
    page = 1
    while page <= 1000:
        status, body = _post_with_retry(
            post_json,
            endpoint,
            _payload(category, page_size, after, language_code, subject),
        )
        kind = response_status(status, body)
        if kind == "auth":
            raise AuthRequired(
                "Preply did not accept the session. Log in to your own account and run the export again."
            )
        if kind != "ok":
            raise PreplyError(f"{label}: unexpected response HTTP {status}")
        payload = json.loads(body)
        current = (payload.get("data") or {}).get("vocabularyBank", {}).get("currentWords")
        if not isinstance(current, dict):
            raise PreplyError(f"{label}: response did not include a vocabulary page")
        batch = current.get("nodes") or []
        nodes.extend(item for item in batch if isinstance(item, dict))
        print(f"{label}: page {page}, {len(nodes)} collected")
        if on_page is not None:
            on_page(label, page, len(nodes))
        if not current.get("hasNext"):
            return nodes
        cursor = current.get("endCursor")
        if not cursor or cursor in seen:
            raise PreplyError(f"{label}: Preply reported more pages but did not return a new cursor")
        seen.add(str(cursor))
        after = cursor
        page += 1
        if delay_seconds:
            time.sleep(delay_seconds)
    raise PreplyError(f"{label}: stopped after 1000 pages")


def _pick_endpoint(
    post_json: PostJson,
    subject: str,
    language_code: str,
    category: str,
    page_size: int,
) -> str:
    payload = probe_payload(subject, language_code, category, min(page_size, 1) or 1)
    last_error = "Vocabulary request failed"
    for path in GRAPHQL_PATHS:
        try:
            status, body = post_json(path, payload)
        except Exception as exc:
            last_error = str(exc)
            continue
        if status == 404:
            continue
        kind = response_status(status, body)
        if kind == "ok":
            return path
        if kind == "auth":
            raise AuthRequired(
                "Preply did not accept the session. Log in to your own account and run the export again."
            )
        last_error = http_error_hint(status, body)
    raise PreplyError(last_error)


def _post_with_retry(post_json: PostJson, endpoint: str, payload: dict) -> tuple[int, str]:
    last: tuple[int, str] = (0, "")
    for attempt in range(1, 4):
        status, body = post_json(endpoint, payload)
        if status == 200 or status in {401, 403, 404}:
            return status, body
        last = (status, body)
        if status in {429, 500, 502, 503, 504} or status == 0:
            time.sleep(attempt)
            continue
        return status, body
    return last


def profile_payload() -> dict:
    return {"operationName": "HeaderUser", "variables": {}, "query": PROFILE_QUERY}


def profile_response_status(status: int, body: str) -> str:
    """Return ok when HeaderUser includes a current user id, otherwise auth or error."""
    if status in {401, 403}:
        return "auth"
    if status >= 400:
        return "error"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "auth" if _looks_like_html(body) else "error"
    if not isinstance(payload, dict):
        return "error"
    data = payload.get("data")
    user = data.get("currentUser") if isinstance(data, dict) else None
    if isinstance(user, dict) and str(user.get("id") or "").strip():
        return "ok"
    messages = " ".join(
        str(item.get("message") or "") for item in (payload.get("errors") or []) if isinstance(item, dict)
    )
    lowered = messages.lower()
    if any(hint in lowered for hint in ("unauth", "not authenticated", "log in", "login", "forbidden", "jwt", "credential")):
        return "auth"
    if messages:
        raise PreplyError(messages)
    if user is None or _looks_like_html(body):
        return "auth"
    return "error"


def parse_learner_profile(payload: dict) -> LearnerProfile:
    """Read id, fullName/firstName, and client.subjects[].subject.alias. Does not invent values."""
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("currentUser") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        raise PreplyError("Preply did not return the signed-in profile.")
    aliases: list[str] = []
    client = user.get("client")
    subjects = client.get("subjects") if isinstance(client, dict) else None
    if isinstance(subjects, list):
        for item in subjects:
            subject = item.get("subject") if isinstance(item, dict) else None
            if not isinstance(subject, dict):
                continue
            alias = subject.get("alias")
            if not isinstance(alias, str):
                continue
            cleaned = alias.strip().lower()
            if cleaned and cleaned not in aliases:
                aliases.append(cleaned)
    return LearnerProfile(
        user_id=str(user.get("id") or "").strip(),
        full_name=_clean_optional_text(user.get("fullName")),
        first_name=_clean_optional_text(user.get("firstName")),
        subject_aliases=tuple(aliases),
    )


def fetch_learner_profile(post_json: PostJson, endpoint: str | None = None) -> LearnerProfile:
    """Load the signed-in learner. A failed call raises; it does not assume english."""
    payload = profile_payload()
    paths: list[str] = []
    if endpoint:
        paths.append(endpoint)
    for path in GRAPHQL_PATHS:
        if path not in paths:
            paths.append(path)
    last_error = "Profile request failed"
    for path in paths:
        try:
            status, body = post_json(path, payload)
        except Exception as exc:
            last_error = str(exc)
            continue
        if status == 404:
            continue
        try:
            kind = profile_response_status(status, body)
        except PreplyError as exc:
            raise PreplyError(f"Не удалось прочитать профиль. Список слов не загружен. {exc}") from exc
        if kind == "ok":
            return parse_learner_profile(json.loads(body))
        if kind == "auth":
            raise AuthRequired(
                "Preply did not accept the session. Log in to your own account and run the export again."
            )
        last_error = http_error_hint(status, body)
    raise PreplyError(f"Не удалось прочитать профиль. Список слов не загружен. {last_error}")


def _clean_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _payload(category: str, page_size: int, after: str | None, language_code: str, subject: str) -> dict:
    return {
        "operationName": "VocabBankOverview",
        "variables": {
            "category": category,
            "first": page_size,
            "after": after,
            "languageCode": language_code,
            "subjectAlias": subject,
        },
        "query": VOCAB_QUERY,
    }


def _looks_like_html(body: str) -> bool:
    stripped = body.lstrip().lower()
    return stripped.startswith("<!doctype") or stripped.startswith("<html")


def http_error_hint(status: int, body: str) -> str:
    """Short status text. Long tokens in the body are not shown."""
    hint = _response_hint(body)
    hint = _redact_secrets(hint)
    hint = re.sub(r"\s+", " ", hint).strip()
    if len(hint) > 180:
        hint = hint[:180].rstrip() + "..."
    if hint:
        return f"HTTP {status}: {hint}"
    return f"HTTP {status}: empty response"


def _response_hint(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list):
            messages = [
                str(item.get("message"))
                for item in errors
                if isinstance(item, dict) and item.get("message")
            ]
            if messages:
                return "; ".join(messages)
        for key in ("detail", "message", "error"):
            if payload.get(key):
                return str(payload[key])
    return body


def _redact_secrets(text: str) -> str:
    text = re.sub(
        r"(?i)(sessionid|csrftoken|csrf|token|authorization|cookie)\s*[=:]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return re.sub(r"[A-Za-z0-9_\-]{24,}", "[redacted]", text)


def cookie_header_value(value: object) -> str | None:
    """A cookie name or value HTTP can send as latin-1, or None when it cannot.

    U+FFFD is never sent. It cannot be encoded as latin-1 and would crash the
    Cookie header.
    """
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = value.decode("latin-1")
    else:
        text = str(value or "")
    if "\ufffd" in text:
        return None
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return None
    return text


class RequestsSession:
    """Cookie-file session for the user's own exported browser cookies."""

    def __init__(self, cookies: list[dict], language_code: str) -> None:
        self._cookies = cookies
        self._language = language_code
        self._session = None

    def post_json(self, path: str, payload: dict) -> tuple[int, str]:
        response = self._client().post(
            ORIGIN + path,
            data=json.dumps(payload).encode("utf-8"),
            timeout=60,
        )
        return response.status_code, response.text

    def get_bytes(self, url: str) -> tuple[int, bytes, str]:
        response = self._client().get(url, timeout=60)
        return response.status_code, response.content, response.headers.get("Content-Type", "")

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _client(self):
        if self._session is not None:
            return self._session
        try:
            import requests
        except ImportError as exc:
            raise PreplyError("Install dependencies: pip install -r requirements.txt") from exc
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json, */*",
                "Content-Type": "application/json",
                "Origin": ORIGIN,
                "Referer": f"{ORIGIN}/en/practice/vocab-bank",
                "x-accept-language": self._language,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }
        )
        for cookie in self._cookies:
            name = cookie_header_value(cookie.get("name"))
            value = cookie_header_value(cookie.get("value"))
            domain = cookie_header_value(cookie.get("domain")) or ".preply.com"
            path = cookie_header_value(cookie.get("path")) or "/"
            if not name or not value:
                continue
            session.cookies.set(name, value, domain=domain, path=path)
        token = session.cookies.get("csrftoken") or session.cookies.get("csrf_token")
        if token:
            session.headers["x-csrftoken"] = token
        self._session = session
        return session


def installed_chrome_path() -> Path | None:
    """Path to system Google Chrome, or None when it is not installed.

    This does not look at Chrome's user-data directory and does not read cookies.
    """
    program = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(program) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    if local:
        candidates.append(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for path in candidates:
        if path.is_file():
            return path
    return None


def preferred_browser_channel() -> str | None:
    """``chrome`` when Google Chrome is installed, otherwise Playwright's own Chromium."""
    if installed_chrome_path() is not None:
        return "chrome"
    return None


class BrowserSession:
    """Persistent browser profile. The user logs in once in the opened window.

    The profile directory is dedicated to this program. It does not read the
    Google cookies from the Chrome profile already used on this computer.
    When Google Chrome is installed, the window is that Chrome (``channel="chrome"``)
    so Google's sign-in page can offer a normal account chooser. The first
    sign-in in this window is what later runs remember.
    """

    def __init__(
        self,
        user_data_dir: str,
        language_code: str,
        subject: str,
        headless: bool,
        login_timeout: float,
        should_stop: Callable[[], bool] | None = None,
        channel: str | None = None,
    ) -> None:
        self.user_data_dir = user_data_dir
        self.language_code = language_code
        self.subject = subject
        self.headless = headless
        self.login_timeout = login_timeout
        self.should_stop = should_stop
        self.channel = channel or None
        self._playwright = None
        self._context = None
        self._page = None
        self._endpoint = GRAPHQL_PATHS[0]

    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PreplyError(
                "Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        launch_kwargs = {
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 900},
            "locale": self.language_code,
        }
        if self.channel:
            launch_kwargs["channel"] = self.channel
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                **launch_kwargs,
            )
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            message = str(exc)
            if "Executable doesn't exist" in message or "Please run the following command" in message:
                raise PreplyError(
                    "Chromium for Playwright is not installed. Run: playwright install chromium"
                ) from exc
            raise PreplyError(f"Could not open the browser profile: {message}") from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        url = self._login_url()
        print(f"Browser profile: {self.user_data_dir}")
        print("Opening your Vocabulary page.")
        print(url)
        print("Log in to your own Preply account in that window if you are not already.")
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            self._wait_until_logged_in()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._page = None
        self._playwright = None
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def post_json(self, path: str, payload: dict) -> tuple[int, str]:
        url = path if path.startswith("http") else ORIGIN + path
        result = self._page.evaluate(
            """async ({url, payload}) => {
                const response = await fetch(url, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'accept': 'application/json',
                        'content-type': 'application/json',
                        'x-accept-language': 'en'
                    },
                    body: JSON.stringify(payload)
                });
                return {status: response.status, text: await response.text()};
            }""",
            {"url": url, "payload": payload},
        )
        return int(result["status"]), str(result["text"])

    def get_bytes(self, url: str) -> tuple[int, bytes, str]:
        response = self._context.request.get(url, timeout=60_000)
        content_type = response.headers.get("content-type", "")
        return response.status, response.body(), content_type

    def _login_url(self) -> str:
        base = f"{ORIGIN}/{self.language_code}/practice/vocab-bank"
        subject = self.subject.strip()
        if not subject:
            return base
        return f"{base}?subject={urllib.parse.quote(subject)}"

    def _wait_for_profile(self) -> None:
        """Sign-in check that does not request a vocabulary list or assume english."""
        deadline = time.time() + self.login_timeout
        payload = profile_payload()
        last = "waiting for login"
        while time.time() < deadline:
            if self.should_stop is not None and self.should_stop():
                raise SessionCancelled("closed")
            if "preply.com" not in (self._page.url or ""):
                self._page.wait_for_timeout(2000)
                continue
            for path in GRAPHQL_PATHS:
                try:
                    status, body = self.post_json(path, payload)
                except Exception as exc:
                    last = str(exc)
                    continue
                if status == 404:
                    continue
                try:
                    kind = profile_response_status(status, body)
                except PreplyError as exc:
                    raise PreplyError(f"Не удалось прочитать профиль. Список слов не загружен. {exc}") from exc
                if kind == "ok":
                    self._endpoint = path
                    print("Session is signed in. Reading your profile.")
                    return
                last = "still on the login page" if kind == "auth" else f"HTTP {status}"
            self._page.wait_for_timeout(2000)
        raise AuthRequired(
            f"No signed-in vocabulary session after {int(self.login_timeout)}s ({last}). "
            "Finish logging in yourself in the browser window, then run the command again."
        )

    def _wait_until_logged_in(self) -> None:
        if not self.subject.strip():
            self._wait_for_profile()
            return
        deadline = time.time() + self.login_timeout
        payload = probe_payload(self.subject, self.language_code, CATEGORIES[0][0], 1)
        last = "waiting for login"
        while time.time() < deadline:
            if self.should_stop is not None and self.should_stop():
                raise SessionCancelled("closed")
            if "preply.com" not in (self._page.url or ""):
                self._page.wait_for_timeout(2000)
                continue
            for path in GRAPHQL_PATHS:
                try:
                    status, body = self.post_json(path, payload)
                except Exception as exc:
                    last = str(exc)
                    continue
                if status == 404:
                    continue
                try:
                    kind = response_status(status, body)
                except PreplyError as exc:
                    raise PreplyError(f"Vocabulary query was rejected: {exc}") from exc
                if kind == "ok":
                    self._endpoint = path
                    print("Session is signed in. Reading your vocabulary.")
                    return
                last = "still on the login page" if kind == "auth" else f"HTTP {status}"
            self._page.wait_for_timeout(2000)
        raise AuthRequired(
            f"No signed-in vocabulary session after {int(self.login_timeout)}s ({last}). "
            "Finish logging in yourself in the browser window, then run the command again."
        )


def public_get_bytes(url: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": ORIGIN + "/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read(8_000_001), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read(200_000)
        return exc.code, body, exc.headers.get("Content-Type", "")
