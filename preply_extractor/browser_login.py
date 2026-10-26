"""Open Preply in the Windows default browser and reuse that browser's own cookies.

Authorize uses ShellExecute (``os.startfile``) on an https URL. The registered
browser handles the URL. Firefox, Chrome, and Edge ask the process that is
already running to open a tab, and they load their normal profile when they
are not running. This module does not start Playwright and does not pass a
separate ``--user-data-dir``.

Cookies are copied out of the locked database and limited to preply.com.
Cookie values are never written to logs or error text.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import tempfile
from ctypes import wintypes
from pathlib import Path

from preply_extractor.preply_api import GRAPHQL_PATHS, RequestsSession

PREPLY_AUTH_URL = "https://preply.com/en/practice/vocab-bank"
_SESSION_COOKIE = "sessionid"
_HTTPS_USER_CHOICE = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"


class NoPreplySession(RuntimeError):
    """The default browser has no signed-in preply.com cookie yet."""


class BrowserCookieError(RuntimeError):
    """The default browser's cookie store could not be read."""


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
        cookies = read_chromium_cookies(chromium_cookie_database(root), chromium_master_key(root))
    return require_preply_session(cookies)


def require_preply_session(cookies: list[dict]) -> list[dict]:
    """Keep preply.com cookies only, and require a non-empty sessionid."""
    kept = [cookie for cookie in cookies if _is_preply_host(str(cookie.get("domain") or ""))]
    signed_in = any(
        str(cookie.get("name") or "") == _SESSION_COOKIE and str(cookie.get("value") or "").strip()
        for cookie in kept
    )
    if not signed_in:
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
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    relative = {
        "chrome": Path("Google") / "Chrome" / "User Data",
        "edge": Path("Microsoft") / "Edge" / "User Data",
        "brave": Path("BraveSoftware") / "Brave-Browser" / "User Data",
    }.get(family)
    if relative is None:
        raise BrowserCookieError(f"No cookie location is known for {family}.")
    root = local / relative
    if not root.is_dir():
        raise BrowserCookieError(f"The {family} profile directory was not found.")
    return root


def chromium_cookie_database(user_data: Path) -> Path:
    profile = _chromium_profile_name(user_data)
    network = user_data / profile / "Network" / "Cookies"
    legacy = user_data / profile / "Cookies"
    if network.is_file():
        return network
    if legacy.is_file():
        return legacy
    raise BrowserCookieError("The browser cookie database was not found.")


def read_firefox_cookies(database: Path) -> list[dict]:
    copy = _copy_sqlite(database)
    try:
        connection = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
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
        connection = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
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
            value = _decrypt_chromium_value(bytes(encrypted), key)
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
    text = host.lstrip(".").lower()
    return text == "preply.com" or text.endswith(".preply.com")


def _copy_sqlite(source: Path) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="preply-cookies-"))
    target = folder / "cookies.sqlite"
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(source) + suffix)
        if extra.is_file():
            shutil.copy2(extra, Path(str(target) + suffix))
    return target


def _decrypt_chromium_value(encrypted: bytes, key: bytes | None) -> str:
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
    return plain.decode("utf-8", errors="replace")


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
