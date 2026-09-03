from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppSettings:
    watch_folder: Path | None = None
    sorted_folder: Path | None = None
    api_key: str = ""
    naming_mode: str = "Automatic"
    filename_format: str = "smart"
    custom_filename_template: str = ""
    watch_and_launch_enabled: bool = False
    # Smart metadata lookup sends a discovered DOI (not document text) to a
    # scholarly metadata service. It is on by default because it is the most
    # reliable way to name ordinary published papers without requiring AI.
    online_metadata_lookup_enabled: bool = True
    allow_cloud_ai_for_word_documents: bool = False
    allow_cloud_ai_for_presentation_documents: bool = False

    def is_complete(self) -> bool:
        return bool(self.watch_folder and self.sorted_folder)

    def clean_naming_mode(self) -> str:
        # "AI" was an older, AI-first label. Smart lookup now uses DOI metadata
        # first and only uses AI as a backup, so preserve old saved settings by
        # migrating that value to the recommended Automatic mode.
        return "Basic" if self.naming_mode == "Basic" else "Automatic"

    def clean_filename_format(self) -> str:
        valid_formats = {
            "smart",
            "journal_compact",
            "journal_detailed",
            "author_year_title",
            "title_year_type",
            "custom",
        }
        return self.filename_format if self.filename_format in valid_formats else "smart"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _as_bool(value: object, default: bool = False) -> bool:
    """Read legacy JSON booleans without treating the string ``"false"`` as true."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _protect_for_current_windows_user(secret: str) -> str:
    """Encrypt a setting with Windows DPAPI so it is tied to this Windows account."""
    if os.name != "nt":
        raise OSError("Windows data protection is unavailable on this platform.")

    source, source_buffer = _blob_from_bytes(secret.encode("utf-8"))
    result = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    protect = crypt32.CryptProtectData
    protect.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DataBlob),
    ]
    protect.restype = ctypes.c_bool
    if not protect(ctypes.byref(source), "AI Paper Sorter API key", None, None, None, 0x1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        protected = ctypes.string_at(result.pbData, result.cbData)
        return base64.b64encode(protected).decode("ascii")
    finally:
        # CryptProtectData allocates output with LocalAlloc.
        kernel32.LocalFree(result.pbData)
        del source_buffer


def _unprotect_for_current_windows_user(encoded_secret: str) -> str:
    if os.name != "nt":
        raise OSError("Windows data protection is unavailable on this platform.")

    source, source_buffer = _blob_from_bytes(base64.b64decode(encoded_secret.encode("ascii")))
    result = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DataBlob),
    ]
    unprotect.restype = ctypes.c_bool
    if not unprotect(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(result.pbData)
        del source_buffer


class SettingsManager:
    """Persist per-user settings without placing API keys beside a shared executable."""

    def __init__(self, script_directory: Path):
        self.script_directory = script_directory
        self.config_path = self._config_path()

    def _config_path(self) -> Path:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "AI Paper Sorter" / "config.json"

    def _legacy_config_path(self) -> Path:
        return self.script_directory / "config.json"

    def _candidate_paths(self) -> tuple[Path, ...]:
        paths = (self._config_path(), self._legacy_config_path())
        return tuple(dict.fromkeys(paths))

    def _configured_path(self, value: object) -> Path | None:
        """Return a stable absolute path for a saved folder setting.

        New Settings saves are already absolute.  This also makes a relative path
        from a hand-edited or old portable config unambiguous for the GUI and the
        background Watch & Launch helper: it is relative to the app location, not
        to whichever working directory happened to start a process.
        """

        if not value:
            return None
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            candidate = self.script_directory / candidate
        try:
            return candidate.resolve()
        except OSError:
            return candidate.absolute()

    @staticmethod
    def _api_key_from_data(data: dict) -> str:
        protected_key = data.get("api_key_protected")
        if protected_key:
            try:
                return _unprotect_for_current_windows_user(str(protected_key))
            except Exception:
                # A DPAPI value cannot be read after copying it to another Windows account.
                return ""
        # Backward compatibility: old versions wrote this value in plain text. It is
        # automatically migrated to DPAPI encryption on the next successful save.
        return str(data.get("api_key", ""))

    def load(self) -> AppSettings:
        for path in self._candidate_paths():
            try:
                with path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                if not isinstance(data, dict):
                    continue
                self.config_path = path
                return AppSettings(
                    watch_folder=self._configured_path(data.get("watch_folder")),
                    sorted_folder=self._configured_path(data.get("sorted_folder")),
                    api_key=self._api_key_from_data(data),
                    naming_mode=str(data.get("naming_mode", "Automatic")),
                    filename_format=str(data.get("filename_format", "smart")),
                    custom_filename_template=str(data.get("custom_filename_template", "")),
                    watch_and_launch_enabled=_as_bool(data.get("watch_and_launch_enabled", False)),
                    online_metadata_lookup_enabled=_as_bool(data.get("online_metadata_lookup_enabled", True), True),
                    allow_cloud_ai_for_word_documents=_as_bool(data.get("allow_cloud_ai_for_word_documents", False)),
                    allow_cloud_ai_for_presentation_documents=_as_bool(
                        data.get("allow_cloud_ai_for_presentation_documents", False)
                    ),
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return AppSettings()

    @staticmethod
    def _write_json_atomically(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix="config-", suffix=".tmp", delete=False
            ) as temporary_file:
                temp_name = temporary_file.name
                json.dump(data, temporary_file, indent=2)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            Path(temp_name).replace(path)
        except Exception:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
            raise

    def _serialized_settings(self, settings: AppSettings) -> dict:
        data = {
            "watch_folder": str(settings.watch_folder),
            "sorted_folder": str(settings.sorted_folder),
            "naming_mode": settings.clean_naming_mode(),
            "filename_format": settings.clean_filename_format(),
            "custom_filename_template": str(settings.custom_filename_template or ""),
            "watch_and_launch_enabled": _as_bool(settings.watch_and_launch_enabled),
            "online_metadata_lookup_enabled": _as_bool(settings.online_metadata_lookup_enabled, True),
            "allow_cloud_ai_for_word_documents": _as_bool(settings.allow_cloud_ai_for_word_documents),
            "allow_cloud_ai_for_presentation_documents": _as_bool(
                settings.allow_cloud_ai_for_presentation_documents
            ),
        }
        if settings.api_key:
            if os.name == "nt":
                data["api_key_protected"] = _protect_for_current_windows_user(settings.api_key)
            else:
                # The released app is Windows-only. Keep source-mode behavior usable on
                # other platforms, without pretending that it has Windows protection.
                data["api_key"] = settings.api_key
        return data

    def _remove_legacy_plaintext_key(self) -> None:
        """Best-effort cleanup after migrating an old portable config to AppData."""
        legacy_path = self._legacy_config_path()
        if legacy_path == self._config_path() or not legacy_path.exists():
            return
        try:
            with legacy_path.open("r", encoding="utf-8") as file:
                legacy_data = json.load(file)
            if not isinstance(legacy_data, dict) or "api_key" not in legacy_data:
                return
            legacy_data.pop("api_key", None)
            legacy_data.pop("api_key_protected", None)
            self._write_json_atomically(legacy_path, legacy_data)
        except (OSError, json.JSONDecodeError, TypeError):
            # The AppData copy has already been safely written; do not make saving
            # Settings fail solely because an old portable config cannot be cleaned.
            return

    def save(self, settings: AppSettings):
        data = self._serialized_settings(settings)
        errors: list[OSError] = []
        # Prefer the per-user config file. The executable directory is a legacy fallback
        # for existing portable installs whose AppData folder is unavailable.
        for path in self._candidate_paths():
            try:
                self._write_json_atomically(path, data)
                self.config_path = path
                if path == self._config_path():
                    self._remove_legacy_plaintext_key()
                return
            except OSError as error:
                errors.append(error)
        detail = f" ({errors[-1]})" if errors else ""
        raise OSError(f"Could not save settings to AppData or the app folder.{detail}")
