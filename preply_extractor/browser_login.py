"""Sign in through a normal Chrome window on this program's own profile.

Google rejects a Playwright launch because it adds ``--enable-automation``.
Authorize starts ``chrome.exe`` with a dedicated ``--user-data-dir`` and reads
preply.com cookies over that window's debugging port. It does not open Opera's
or Chrome's cookie database, and it does not attach to those everyday windows.

``open_in_default_browser`` remains for the ShellExecute behavior itself.
Cookie values are never written to logs or error text.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse

from preply_extractor.preply_api import (
    GRAPHQL_PATHS,
    RequestsSession,
    browser_context_closed,
    cookie_header_value,
    installed_chrome_path,
)

PREPLY_AUTH_URL = "https://preply.com/en/practice/vocab-bank"
LOGIN_DEBUG_PORT = 9333
_SESSION_COOKIE = "sessionid"
_HTTPS_USER_CHOICE = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_FILE_SHARE_DELETE = 0x4
_FILE_SHARE_ALL = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_SHARING_VIOLATION = 32
_SHARE_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 0.2
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_KERNEL32 = None


class NoPreplySession(RuntimeError):
    """The default browser has no signed-in preply.com cookie yet."""


class BrowserCookieError(RuntimeError):
    """The default browser's cookie store could not be read."""


class LoginWindowClosed(RuntimeError):
    """The separate sign-in window was closed before sign-in finished."""


class ChromeNotInstalled(BrowserCookieError):
    """Google Chrome is not installed, so Google sign-in cannot be opened."""


class UnreadableSessionCookie(BrowserCookieError):
    """The preply.com session cookie cannot be sent in an HTTP header."""


class BrowserCookieSession:
    """requests session built from the user's own preply.com cookies."""

    def __init__(self, cookies: list[dict], language_code: str) -> None:
        self._inner = RequestsSession(cookies, language_code)
        self.endpoint = GRAPHQL_PATHS[0]

    def post_json(self, path: str, payload: dict) -> tuple[int, str]:
        return self._inner.post_json(path, payload)

    def get_bytes(self, url: str) -> tuple[int, bytes, str]:
        return self._inner.get_bytes(url)

    def close(self) -> None:
        self._inner.close()


def _reject_foreign_profile(path: Path) -> None:
    """This window may use only the program profile, never Opera or everyday Chrome."""
    text = str(path).lower().replace("/", "\\")
    if "opera software" in text:
        raise BrowserCookieError("The sign-in window cannot use the main Opera profile.")
    if "\\google\\chrome\\user data" in text:
        raise BrowserCookieError("The sign-in window cannot use the main Chrome profile.")


def chrome_sign_in_command(executable: Path, user_data_dir: Path, url: str, port: int = LOGIN_DEBUG_PORT) -> list[str]:
    """Normal Chrome argv. No automation flag, no headless mode, no default profile."""
    profile = Path(user_data_dir).resolve()
    _reject_foreign_profile(profile)
    if not str(url).startswith("https://"):
        raise BrowserCookieError("Only an https Preply address can be opened.")
    return [
        str(executable),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def debug_port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _cdp_websocket_call(ws_url: str, port: int, commands: list[dict]) -> dict[int, dict]:
    """CDP commands on one localhost page socket. The Chrome process stays open."""
    parsed = urlparse(ws_url)
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"} or parsed.port != port:
        raise LoginWindowClosed("sign-in window was closed")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    connect_host = "::1" if host == "::1" else "127.0.0.1"
    try:
        sock = socket.create_connection((connect_host, port), timeout=5)
    except OSError as exc:
        raise LoginWindowClosed("sign-in window was closed") from exc
    try:
        sock.settimeout(5)
        sock.sendall(request.encode("ascii"))
        buffer = bytearray()
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise LoginWindowClosed("sign-in window was closed")
            buffer.extend(chunk)
        head, rest = bytes(buffer).split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise LoginWindowClosed("sign-in window was closed")
        pending = bytearray(rest)
        results: dict[int, dict] = {}
        for command in commands:
            sock.sendall(_ws_client_text(json.dumps(command).encode("utf-8")))
            while command.get("id") not in results:
                text = _ws_next_text(sock, pending)
                message = json.loads(text)
                command_id = message.get("id")
                if command_id is None:
                    continue
                if message.get("error"):
                    raise BrowserCookieError("The window could not be read.")
                result = message.get("result")
                results[command_id] = result if isinstance(result, dict) else {}
        return results
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _ws_client_text(payload: bytes) -> bytes:
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(length.to_bytes(8, "big"))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + masked


def _ws_next_text(sock: socket.socket, pending: bytearray) -> str:
    while True:
        while len(pending) < 2:
            chunk = sock.recv(4096)
            if not chunk:
                raise LoginWindowClosed("sign-in window was closed")
            pending.extend(chunk)
        length = pending[1] & 0x7F
        offset = 2
        if length == 126:
            while len(pending) < 4:
                pending.extend(_recv_some(sock))
            length = int.from_bytes(pending[2:4], "big")
            offset = 4
        elif length == 127:
            while len(pending) < 10:
                pending.extend(_recv_some(sock))
            length = int.from_bytes(pending[2:10], "big")
            offset = 10
        masked = (pending[1] & 0x80) != 0
        if masked:
            while len(pending) < offset + 4:
                pending.extend(_recv_some(sock))
            mask = bytes(pending[offset : offset + 4])
            offset += 4
        else:
            mask = b""
        while len(pending) < offset + length:
            pending.extend(_recv_some(sock))
        payload = bytes(pending[offset : offset + length])
        opcode = pending[0] & 0x0F
        del pending[: offset + length]
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x1:
            return payload.decode("utf-8")
        if opcode == 0x8:
            raise LoginWindowClosed("sign-in window was closed")


def _recv_some(sock: socket.socket) -> bytes:
    chunk = sock.recv(4096)
    if not chunk:
        raise LoginWindowClosed("sign-in window was closed")
    return chunk


def _debug_targets(port: int) -> list:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LoginWindowClosed("sign-in window was closed") from exc
    if not isinstance(payload, list):
        raise LoginWindowClosed("sign-in window was closed")
    return payload


def _page_websocket(targets: list, port: int) -> str:
    """Page socket for preply.com. The browser socket does not implement getAllCookies."""
    fallback = ""
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        socket_url = str(target.get("webSocketDebuggerUrl") or "")
        parsed = urlparse(socket_url)
        if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"} or parsed.port != port:
            continue
        host = (urlparse(str(target.get("url") or "")).hostname or "").lower().strip(".")
        if host == "preply.com" or host.endswith(".preply.com"):
            return socket_url
        if not fallback:
            fallback = socket_url
    if not fallback:
        raise LoginWindowClosed("sign-in window was closed")
    return fallback


def cdp_network_cookies(port: int = LOGIN_DEBUG_PORT) -> list[dict]:
    """``Network.getAllCookies`` from the Preply page. Does not open a cookie file."""
    page_socket = _page_websocket(_debug_targets(port), port)
    try:
        results = _cdp_websocket_call(
            page_socket,
            port,
            [
                {"id": 1, "method": "Network.enable"},
                {"id": 2, "method": "Network.getAllCookies"},
            ],
        )
    except LoginWindowClosed:
        raise
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise LoginWindowClosed("sign-in window was closed") from exc
    result = results.get(2) if isinstance(results, dict) else None
    cookies = result.get("cookies") if isinstance(result, dict) else None
    if not isinstance(cookies, list):
        raise BrowserCookieError("The window did not return cookies.")
    return cookies


class SeparateProfileWindow:
    """Normal Chrome on this program's profile. Opera and everyday Chrome stay untouched."""

    def __init__(self, user_data_dir: Path | str, channel: str | None = None) -> None:
        resolved = Path(user_data_dir).resolve()
        _reject_foreign_profile(resolved)
        self.user_data_dir = str(resolved)
        self._process: subprocess.Popen | None = None

    def _process_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def is_open(self) -> bool:
        return self._process_alive() or debug_port_is_open(LOGIN_DEBUG_PORT)

    def open(self, url: str) -> None:
        """Start Chrome when the previous dedicated window is gone. Never starts a second one."""
        if not url.startswith("https://"):
            raise BrowserCookieError("Only an https Preply address can be opened.")
        if self._process_alive() or debug_port_is_open(LOGIN_DEBUG_PORT):
            return
        chrome = installed_chrome_path()
        if chrome is None:
            raise ChromeNotInstalled("Google Chrome is not installed")
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        command = chrome_sign_in_command(chrome, Path(self.user_data_dir), url, LOGIN_DEBUG_PORT)
        self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def read_preply_cookies(self) -> list[dict]:
        """preply.com cookies from the debug port. Does not open a cookie database."""
        if not self.is_open:
            raise LoginWindowClosed("sign-in window was closed")
        try:
            raw = cdp_network_cookies(LOGIN_DEBUG_PORT)
        except LoginWindowClosed:
            if self._process_alive():
                raise NoPreplySession("no preply.com session cookie")
            raise
        return cookies_from_browser_api(raw)

    def close(self) -> None:
        """Stop only the Chrome process this window started."""
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        except Exception:
            return


def context_was_closed(exc: BaseException) -> bool:
    """Playwright reports a window the user closed with TargetClosedError."""
    return isinstance(exc, LoginWindowClosed) or browser_context_closed(exc)


def cookies_from_browser_api(cookies: list[dict]) -> list[dict]:
    """Normalize cookies from ``context.cookies()``.

    Chromium's on-disk host-hash prefix is removed when it is present.
    Values that cannot be sent as latin-1 are left for the session check to
    reject. This function does not open Opera's cookie file.
    """
    prepared: list[dict] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if not _is_preply_host(domain):
            continue
        value = _api_cookie_text(cookie.get("value"), domain)
        prepared.append(
            {
                "domain": domain,
                "name": str(cookie.get("name") or ""),
                "value": value if value is not None else str(cookie.get("value") or ""),
                "path": str(cookie.get("path") or "/"),
            }
        )
    return require_preply_session(prepared)


def _api_cookie_text(value: object, host: str) -> str | None:
    if isinstance(value, bytes):
        raw = value
    else:
        text = str(value or "")
        if "\ufffd" in text:
            return None
        try:
            raw = text.encode("latin-1")
        except UnicodeEncodeError:
            return None
    text = _cookie_text(_strip_host_bound_prefix(raw, host))
    if "\ufffd" in text:
        return None
    return text


def open_in_default_browser(url: str, opener=None) -> None:
    """Open ``url`` with the registered https handler.

    ``os.startfile`` is ShellExecute. A browser that is already running receives
    a new tab in that process; a browser that is not running starts with its
    normal profile. No custom profile directory is passed.
    """
    if not url.startswith("https://"):
        raise BrowserCookieError("Only an https Preply address can be opened.")
    launch = opener or os.startfile
    launch(url)


def read_https_progid() -> str:
    """ProgId of the user's default https browser, from the UserChoice key."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HTTPS_USER_CHOICE) as key:
        value, _kind = winreg.QueryValueEx(key, "ProgId")
    progid = str(value).strip()
    if not progid:
        raise BrowserCookieError("The default https browser is not set.")
    return progid


def browser_family(progid: str) -> str:
    """Map a UserChoice ProgId to the cookie store it belongs to."""
    text = progid.strip().lower()
    if "firefox" in text:
        return "firefox"
    if "opera" in text:
        if "gx" in text:
            return "opera-gx"
        return "opera"
    if "edg" in text:
        return "edge"
    if "brave" in text:
        return "brave"
    if "chrome" in text:
        return "chrome"
    raise BrowserCookieError(f"The default browser ({progid}) is not one this window can read cookies from.")


def load_default_browser_preply_cookies(progid: str | None = None) -> list[dict]:
    """preply.com cookies from the default browser. Raises when sign-in is missing."""
    family = browser_family(progid or read_https_progid())
    if family == "firefox":
        cookies = read_firefox_cookies(firefox_cookie_database())
    else:
        root = chromium_user_data_dir(family)
        cookies = read_chromium_cookies(
            chromium_cookie_database(root, family),
            chromium_master_key(root),
        )
    return require_preply_session(cookies)


def require_preply_session(cookies: list[dict]) -> list[dict]:
    """Keep preply.com cookies only, and require a non-empty sessionid.

    A cookie whose name or value cannot be sent as latin-1 is dropped. If that
    cookie is the session itself, the caller gets a short error instead of a
    header-encoding traceback.
    """
    kept: list[dict] = []
    saw_session = False
    saw_unreadable_session = False
    for cookie in cookies:
        if not _is_preply_host(str(cookie.get("domain") or "")):
            continue
        raw_name = str(cookie.get("name") or "")
        raw_value = str(cookie.get("value") or "")
        name = cookie_header_value(cookie.get("name"))
        value = cookie_header_value(cookie.get("value"))
        if raw_name == _SESSION_COOKIE and raw_value.strip() and (name != _SESSION_COOKIE or not value or not value.strip()):
            saw_unreadable_session = True
            continue
        if not name or value is None:
            continue
        if name == _SESSION_COOKIE and value.strip():
            saw_session = True
        kept.append(
            {
                "domain": cookie_header_value(cookie.get("domain")) or ".preply.com",
                "name": name,
                "value": value,
                "path": cookie_header_value(cookie.get("path")) or "/",
            }
        )
    if not saw_session:
        if saw_unreadable_session:
            raise UnreadableSessionCookie("session cookie is unreadable")
        raise NoPreplySession("no preply.com session cookie")
    return kept


def firefox_cookie_database() -> Path:
    ini = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "profiles.ini"
    if not ini.is_file():
        raise BrowserCookieError("Firefox is the default browser, but its profile list was not found.")
    profile = _firefox_profile_dir(ini)
    database = profile / "cookies.sqlite"
    if not database.is_file():
        raise BrowserCookieError("Firefox is the default browser, but its cookie database was not found.")
    return database


def chromium_user_data_dir(family: str) -> Path:
    if family in {"opera", "opera-gx"}:
        base = Path(os.environ.get("APPDATA", ""))
        relative = {
            "opera": Path("Opera Software") / "Opera Stable",
            "opera-gx": Path("Opera Software") / "Opera GX Stable",
        }[family]
    else:
        base = Path(os.environ.get("LOCALAPPDATA", ""))
        relative = {
            "chrome": Path("Google") / "Chrome" / "User Data",
            "edge": Path("Microsoft") / "Edge" / "User Data",
            "brave": Path("BraveSoftware") / "Brave-Browser" / "User Data",
        }.get(family)
    if relative is None:
        raise BrowserCookieError(f"No cookie location is known for {family}.")
    root = base / relative
    if not root.is_dir():
        raise BrowserCookieError(f"The {family} profile directory was not found.")
    return root


def chromium_cookie_database(user_data: Path, family: str | None = None) -> Path:
    """Cookie sqlite for a Chromium user-data directory.

    Opera keeps ``Network/Cookies`` or ``Cookies`` directly in its profile
    directory. Chrome, Edge, and Brave keep them inside the last-used profile.
    """
    if family in {"opera", "opera-gx"}:
        direct = _cookie_file(user_data)
        if direct is not None:
            return direct
    profile = _chromium_profile_name(user_data)
    nested = _cookie_file(user_data / profile)
    if nested is not None:
        return nested
    raise BrowserCookieError("The browser cookie database was not found.")


def _cookie_file(directory: Path) -> Path | None:
    network = directory / "Network" / "Cookies"
    legacy = directory / "Cookies"
    if network.is_file():
        return network
    if legacy.is_file():
        return legacy
    return None


def read_firefox_cookies(database: Path) -> list[dict]:
    copy = _copy_sqlite(database)
    try:
        connection = sqlite3.connect(_temp_sqlite_uri(copy), uri=True)
        try:
            rows = connection.execute(
                "SELECT host, name, value, path FROM moz_cookies"
            ).fetchall()
        finally:
            connection.close()
    finally:
        shutil.rmtree(copy.parent, ignore_errors=True)
    cookies = []
    for host, name, value, path in rows:
        if not _is_preply_host(str(host or "")):
            continue
        cookies.append(
            {
                "domain": str(host or ".preply.com"),
                "name": str(name or ""),
                "value": str(value or ""),
                "path": str(path or "/"),
            }
        )
    return cookies


def read_chromium_cookies(database: Path, key: bytes | None) -> list[dict]:
    copy = _copy_sqlite(database)
    try:
        connection = sqlite3.connect(_temp_sqlite_uri(copy), uri=True)
        try:
            rows = connection.execute(
                "SELECT host_key, name, value, encrypted_value, path FROM cookies"
            ).fetchall()
        finally:
            connection.close()
    finally:
        shutil.rmtree(copy.parent, ignore_errors=True)
    cookies = []
    for host, name, plain, encrypted, path in rows:
        if not _is_preply_host(str(host or "")):
            continue
        value = str(plain or "")
        if not value and encrypted:
            raw = _decrypt_chromium_bytes(bytes(encrypted), key)
            raw = _strip_host_bound_prefix(raw, str(host or ""))
            value = _cookie_text(raw)
        cookies.append(
            {
                "domain": str(host or ".preply.com"),
                "name": str(name or ""),
                "value": value,
                "path": str(path or "/"),
            }
        )
    return cookies


def chromium_master_key(user_data: Path) -> bytes | None:
    local_state = user_data / "Local State"
    if not local_state.is_file():
        return None
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
        encoded = payload.get("os_crypt", {}).get("encrypted_key")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(encoded, str) or not encoded:
        return None
    import base64

    raw = base64.b64decode(encoded)
    if raw.startswith(b"DPAPI"):
        raw = raw[5:]
    return _dpapi_decrypt(raw)


def _firefox_profile_dir(ini_path: Path) -> Path:
    install_default = ""
    section_default = ""
    current_path = ""
    current_relative = True
    current_default = False
    base = ini_path.parent

    def consider() -> None:
        nonlocal section_default
        if current_default and current_path and not section_default:
            section_default = current_path if not current_relative else str(base / current_path)

    for raw in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            consider()
            current_path = ""
            current_relative = True
            current_default = False
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "default" and line.lower().startswith("default=") and not current_path:
            if not install_default:
                install_default = value
        if key == "path":
            current_path = value
        elif key == "isrelative":
            current_relative = value != "0"
        elif key == "default":
            current_default = value == "1"
    consider()
    chosen = install_default or section_default
    if not chosen:
        raise BrowserCookieError("Firefox's default profile was not found.")
    path = Path(chosen)
    if not path.is_absolute():
        path = base / path
    return path


def _chromium_profile_name(user_data: Path) -> str:
    local_state = user_data / "Local State"
    if not local_state.is_file():
        return "Default"
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
        name = payload.get("profile", {}).get("last_used")
    except (OSError, json.JSONDecodeError, AttributeError):
        return "Default"
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "Default"


def _is_preply_host(host: str) -> bool:
    text = host.strip().lower().strip(".")
    return text == "preply.com" or text.endswith(".preply.com")


def _temp_sqlite_uri(path: Path) -> str:
    """Read-only URI for a temp copy. Never used on the live browser file."""
    return f"file:{path.as_posix()}?mode=ro&immutable=1"


def _copy_sqlite(source: Path) -> Path:
    """Copy a cookie database that the browser may still have open.

    The bytes are read into a temp directory before SQLite opens anything.
    ``shutil.copy2`` and ``open()`` are not used on the live file: both raise
    WinError 32 while the browser holds it. Journal, WAL, and SHM siblings
    use the same shared read. SQLite then opens only the temp copy.
    """
    folder = Path(tempfile.mkdtemp(prefix="preply-cookies-"))
    target = folder / "cookies.sqlite"
    _copy_bytes_shared(source, target)
    for suffix in _SQLITE_SIDECARS:
        extra = Path(str(source) + suffix)
        try:
            _copy_bytes_shared(extra, Path(str(target) + suffix))
        except FileNotFoundError:
            continue
    return target


def _copy_bytes_shared(source: Path, target: Path) -> None:
    try:
        data = _shared_create_read(source)
    except FileNotFoundError:
        raise
    except BrowserCookieError:
        raise
    except OSError as exc:
        raise BrowserCookieError(str(exc)) from exc
    target.write_bytes(data)


def _shared_create_read(path: Path) -> bytes:
    """Read ``path`` while another process has it open.

    Shared ``CreateFileW`` is tried first. ``FILE_FLAG_BACKUP_SEMANTICS`` is
    tried only after that fails. SeBackupPrivilege is not enabled.
    """
    try:
        return _open_attempts(path, _FILE_ATTRIBUTE_NORMAL)
    except FileNotFoundError:
        raise
    except OSError as shared_error:
        backup_error: OSError = shared_error
        try:
            return _open_attempts(path, _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_BACKUP_SEMANTICS)
        except FileNotFoundError:
            raise
        except OSError as exc:
            backup_error = exc
        holder = _locking_process_names(path)
        message = str(backup_error).strip() or str(shared_error).strip()
        role = _cookie_file_role(path.name)
        if holder:
            detail = f"{path.name} ({role}) is held by {holder}"
        else:
            detail = f"{path.name} ({role})"
        raise BrowserCookieError(f"{message} {detail}".strip()) from backup_error


def _open_attempts(path: Path, flags: int) -> bytes:
    last: OSError | None = None
    for attempt in range(_SHARE_ATTEMPTS):
        try:
            return _read_with_shared_handle(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            last = exc
            if getattr(exc, "winerror", None) != _SHARING_VIOLATION or attempt == _SHARE_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_DELAY_SECONDS)
    assert last is not None
    raise last


def _cookie_file_role(name: str) -> str:
    lower = name.lower()
    if lower.endswith("-journal"):
        return "cookie journal"
    if lower.endswith("-wal"):
        return "cookie wal"
    if lower.endswith("-shm"):
        return "cookie shm"
    return "cookie database"


def _read_with_shared_handle(path: Path, flags: int = _FILE_ATTRIBUTE_NORMAL) -> bytes:
    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if _handle_invalid(handle):
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(error, "The cookie file was not found.", str(path))
        raise ctypes.WinError(error)
    try:
        return _read_handle(kernel32, handle)
    finally:
        kernel32.CloseHandle(handle)


def _read_handle(kernel32, handle) -> bytes:
    chunks: list[bytes] = []
    while True:
        size = 1024 * 1024
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD(0)
        ok = kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value == 0:
            break
        chunks.append(buffer.raw[: read.value])
    return b"".join(chunks)


def _handle_invalid(handle) -> bool:
    if not handle:
        return True
    value = int(handle)
    return value in {0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF}


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        library.CreateFileW.restype = wintypes.HANDLE
        library.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        library.ReadFile.restype = wintypes.BOOL
        library.CloseHandle.argtypes = [wintypes.HANDLE]
        library.CloseHandle.restype = wintypes.BOOL
        _KERNEL32 = library
    return _KERNEL32


def _rstrtmgr():
    return ctypes.WinDLL("rstrtmgr", use_last_error=True)


def _locking_process_names(path: Path) -> str:
    """Name the process that has ``path`` open. Does not close that process."""
    try:
        return _restart_manager_holders(path)
    except Exception:
        return ""


def _restart_manager_holders(path: Path) -> str:
    """Restart Manager list only. ``RmShutdown`` is never called."""
    library = _rstrtmgr()

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class UniqueProcess(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", FileTime)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [
            ("Process", UniqueProcess),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", wintypes.DWORD),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    if isinstance(library, ctypes.WinDLL):
        library.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
        library.RmStartSession.restype = wintypes.DWORD
        library.RmRegisterResources.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            ctypes.POINTER(wintypes.LPCWSTR),
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.c_void_p,
        ]
        library.RmRegisterResources.restype = wintypes.DWORD
        library.RmGetList.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(ProcessInfo),
            ctypes.POINTER(wintypes.DWORD),
        ]
        library.RmGetList.restype = wintypes.DWORD
        library.RmEndSession.argtypes = [wintypes.DWORD]
        library.RmEndSession.restype = wintypes.DWORD

    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(64)
    if library.RmStartSession(ctypes.pointer(session), 0, key) != 0:
        return ""
    try:
        files = (wintypes.LPCWSTR * 1)(str(path))
        if library.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return ""
        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reason = wintypes.DWORD(0)
        status = library.RmGetList(
            session,
            ctypes.pointer(needed),
            ctypes.pointer(count),
            None,
            ctypes.pointer(reason),
        )
        if needed.value == 0:
            return ""
        if status not in (0, 234):
            return ""
        infos = (ProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        status = library.RmGetList(
            session,
            ctypes.pointer(needed),
            ctypes.pointer(count),
            infos,
            ctypes.pointer(reason),
        )
        if status != 0:
            return ""
        names: list[str] = []
        for index in range(count.value):
            text = str(infos[index].strAppName).split("\x00", 1)[0].strip()
            if text and text not in names:
                names.append(text)
        return ", ".join(names)
    finally:
        library.RmEndSession(session)


def _decrypt_chromium_value(encrypted: bytes, key: bytes | None) -> str:
    return _cookie_text(_decrypt_chromium_bytes(encrypted, key))


def _decrypt_chromium_bytes(encrypted: bytes, key: bytes | None) -> bytes:
    if encrypted.startswith(b"v20"):
        raise BrowserCookieError("This browser's cookie encryption could not be read.")
    try:
        if encrypted.startswith((b"v10", b"v11")):
            if not key:
                raise BrowserCookieError("This browser's cookie key could not be read.")
            nonce = encrypted[3:15]
            body = encrypted[15:]
            if len(body) < 16:
                raise BrowserCookieError("This browser's cookie encryption could not be read.")
            plain = _aes_gcm_decrypt(key, nonce, body[:-16], body[-16:])
        else:
            plain = _dpapi_decrypt(encrypted)
    except BrowserCookieError:
        raise
    except Exception as exc:
        raise BrowserCookieError("This browser's cookie encryption could not be read.") from exc
    return plain


def _strip_host_bound_prefix(raw: bytes, host: str) -> bytes:
    """Drop Chromium's SHA-256(host) prefix when the decrypted cookie has one.

    Current Chrome and Opera store that 32-byte hash in front of the value.
    Sending it makes the Cookie header binary, and Preply answers HTTP 400.
    """
    if len(raw) < 32 or not host:
        return raw
    digest = hashlib.sha256(host.encode("utf-8")).digest()
    if raw[:32] == digest:
        return raw[32:]
    return raw


def _cookie_text(raw: bytes) -> str:
    """UTF-8 when every byte is valid UTF-8; otherwise latin-1.

    latin-1 keeps the original bytes. ``errors='replace'`` would insert U+FFFD,
    which an HTTP Cookie header cannot encode.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _dpapi_decrypt(encrypted: bytes) -> bytes:
    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(encrypted, len(encrypted))
    blob_in = DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise BrowserCookieError("This browser's cookie encryption could not be read.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class _GcmInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbNonce", wintypes.ULONG),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbAuthData", wintypes.ULONG),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbTag", wintypes.ULONG),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", wintypes.ULONG),
    ]


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    bcrypt = ctypes.WinDLL("bcrypt")
    algorithm = ctypes.c_void_p()
    status = bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), ctypes.c_wchar_p("AES"), None, 0)
    if status:
        raise BrowserCookieError("This browser's cookie encryption could not be read.")
    try:
        mode = ctypes.create_unicode_buffer("ChainingModeGCM")
        status = bcrypt.BCryptSetProperty(
            algorithm,
            ctypes.c_wchar_p("ChainingMode"),
            mode,
            ctypes.sizeof(mode),
            0,
        )
        if status:
            raise BrowserCookieError("This browser's cookie encryption could not be read.")
        key_handle = ctypes.c_void_p()
        key_buffer = ctypes.create_string_buffer(key, len(key))
        status = bcrypt.BCryptGenerateSymmetricKey(
            algorithm,
            ctypes.byref(key_handle),
            None,
            0,
            key_buffer,
            len(key),
            0,
        )
        if status:
            raise BrowserCookieError("This browser's cookie encryption could not be read.")
        try:
            info = _GcmInfo()
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = 1
            nonce_buffer = ctypes.create_string_buffer(nonce, len(nonce))
            tag_buffer = ctypes.create_string_buffer(tag, len(tag))
            info.pbNonce = ctypes.cast(nonce_buffer, ctypes.POINTER(ctypes.c_ubyte))
            info.cbNonce = len(nonce)
            info.pbTag = ctypes.cast(tag_buffer, ctypes.POINTER(ctypes.c_ubyte))
            info.cbTag = len(tag)
            output = ctypes.create_string_buffer(len(ciphertext) + 16)
            written = wintypes.ULONG(0)
            cipher_buffer = ctypes.create_string_buffer(ciphertext, len(ciphertext))
            status = bcrypt.BCryptDecrypt(
                key_handle,
                cipher_buffer,
                len(ciphertext),
                ctypes.byref(info),
                None,
                0,
                output,
                len(output),
                ctypes.byref(written),
                0,
            )
            if status:
                raise BrowserCookieError("This browser's cookie encryption could not be read.")
            return output.raw[: written.value]
        finally:
            bcrypt.BCryptDestroyKey(key_handle)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
