"""Open Preply in the Windows default browser and reuse that browser's own cookies.

Authorize uses ShellExecute (``os.startfile``) on an https URL. The registered
browser handles the URL. Firefox, Chrome, Edge, and Opera ask the process
that is already running to open a tab, and they load their normal profile
when they are not running. This module does not start Playwright and does
not pass a separate ``--user-data-dir``.

Cookies are copied with a shared read while the browser keeps the database
open, then limited to preply.com. Cookie values are never written to logs
or error text.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from preply_extractor.preply_api import GRAPHQL_PATHS, RequestsSession

PREPLY_AUTH_URL = "https://preply.com/en/practice/vocab-bank"
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
