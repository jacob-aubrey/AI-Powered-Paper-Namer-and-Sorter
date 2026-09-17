"""
Continuously watches To Sort and opens AI Paper Sorter as a document appears.
The GUI owns readiness checks; reconciliation recovers missed native events.
"""

import sys
import os
import time
import subprocess
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from document_types import is_processable_document
from settings import SettingsManager


LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False
SETTINGS_CHECK_SECONDS = 5.0
RECONCILE_SECONDS = 2.0
LAUNCH_RETRY_SECONDS = 5.0


def _script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()


SCRIPT_DIR = _script_dir()
GUI_PY = SCRIPT_DIR / "main.py"
GUI_MUTEX_NAME = "Local\\LitSorterGuiMode"
WATCHER_MUTEX_NAME = "Local\\LitSorterWatchMode"
launched_gui_process: subprocess.Popen | None = None
watcher_mutex_handle = None


def watcher_log_path() -> Path:
    return SettingsManager(SCRIPT_DIR)._config_path().parent / "watcher.log"


def configure_logging():
    """Keep background failures visible even in a windowed executable."""
    try:
        path = watcher_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(handler, RotatingFileHandler) for handler in LOGGER.handlers):
            handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [PID %(process)d] %(levelname)s %(message)s"))
            LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
    except OSError:
        # Login may precede profile availability. NullHandler stays safe until
        # logging can be retried by the main loop.
        pass


def gui_launch_command() -> tuple[list[str], Path]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable))], Path(sys.executable).parent
    return [sys.executable, str(GUI_PY)], SCRIPT_DIR


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def acquire_single_watcher_lock() -> bool:
    global watcher_mutex_handle
    if os.name != "nt":
        return True
    kernel32 = _kernel32()
    watcher_mutex_handle = kernel32.CreateMutexW(None, False, WATCHER_MUTEX_NAME)
    error = ctypes.get_last_error()
    if not watcher_mutex_handle:
        LOGGER.error("Could not acquire the watcher lock: Windows error %s", error)
        return False
    if error == 183:
        kernel32.CloseHandle(watcher_mutex_handle)
        watcher_mutex_handle = None
        LOGGER.warning("Another Watch and Launch helper already owns the session lock; this helper is exiting.")
        return False
    return True


def release_single_watcher_lock():
    global watcher_mutex_handle
    if os.name == "nt" and watcher_mutex_handle is not None:
        _kernel32().CloseHandle(watcher_mutex_handle)
        watcher_mutex_handle = None


def _named_mutex_exists(name: str) -> bool:
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    handle = kernel32.OpenMutexW(0x00100000, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5


def is_watcher_running() -> bool:
    """Report the actual session helper, independently of saved settings."""
    return _named_mutex_exists(WATCHER_MUTEX_NAME)


def is_gui_running() -> bool:
    if launched_gui_process is not None and launched_gui_process.poll() is None:
        return True
    return _named_mutex_exists(GUI_MUTEX_NAME)


class WatchDisabledError(FileNotFoundError):
    """The user explicitly disabled Watch and Launch."""


def load_folder_settings():
    settings = SettingsManager(SCRIPT_DIR).load()
    if not settings.is_complete():
        raise FileNotFoundError("Folder settings are unavailable; waiting for saved settings.")
    if not settings.watch_and_launch_enabled:
        raise WatchDisabledError("Watch and Launch is disabled in Settings.")
    watch_folder = settings.watch_folder.expanduser().resolve()
    sorted_folder = settings.sorted_folder.expanduser().resolve()
    watch_folder.mkdir(parents=True, exist_ok=True)
    # Opening the inbox must not depend on the output drive being mounted.
    # The GUI validates/creates the output folder when it needs to use it.
    return watch_folder, sorted_folder


def launch_gui() -> bool:
    global launched_gui_process
    try:
        command, working_directory = gui_launch_command()
        launch_options = {}
        if getattr(sys, "frozen", False):
            launch_options["env"] = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        launched_gui_process = subprocess.Popen(
            command, cwd=str(working_directory),
            creationflags=0x08000000 if os.name == "nt" else 0,
            **launch_options,
        )
        LOGGER.info("Started GUI (PID %s) from %s", launched_gui_process.pid, command[0])
        return True
    except Exception as exc:
        LOGGER.error("Could not launch the GUI; will retry: %s", exc)
        return False


class LaunchOnDocumentEvent(FileSystemEventHandler):
    def __init__(self, watch_folder: Path | None = None):
        super().__init__()
        self._watch_folder = watch_folder.resolve() if watch_folder is not None else None
        self._launch_lock = threading.RLock()
        self._seen_signatures = {}
        self._pending_paths = {}
        self._next_retry = 0.0
        self._last_activation = float("-inf")

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path.absolute()))

    def _handle_document(self, path: Path):
        if not is_processable_document(path):
            return
        try:
            if self._watch_folder is not None and path.resolve().parent != self._watch_folder:
                return
            if not path.is_file():
                return
            stat = path.stat()
            signature = stat.st_size, stat.st_mtime_ns
        except OSError:
            return
        key = self._key(path)
        with self._launch_lock:
            if self._seen_signatures.get(key) == signature:
                return
            self._seen_signatures[key] = signature
            if self._pending_paths and time.monotonic() < self._next_retry:
                self._pending_paths[key] = path
                return
            # Do not wait for complete downloads on the watchdog dispatcher.
            if self._maybe_launch_gui():
                self._pending_paths.pop(key, None)
            else:
                self._pending_paths[key] = path
                self._next_retry = time.monotonic() + LAUNCH_RETRY_SECONDS

    def _maybe_launch_gui(self):
        with self._launch_lock:
            if not is_gui_running():
                return launch_gui()
            # A running but minimized app looked like a failed launch. Restore
            # it for a new/changed document, but never on unchanged scan results.
            now = time.monotonic()
            if now - self._last_activation >= 1.0:
                try:
                    from main import activate_existing_gui
                    activate_existing_gui(timeout=0)
                except (OSError, AttributeError) as exc:
                    LOGGER.warning("Could not restore the existing GUI: %s", exc)
                self._last_activation = now
            return True

    def reconcile(self):
        """Catch missed events without reopening unchanged, skipped papers."""
        if self._watch_folder is None:
            return
        paths = list(self._watch_folder.iterdir())
        present = {self._key(path) for path in paths if is_processable_document(path)}
        for path in paths:
            self._handle_document(path)
        with self._launch_lock:
            for key in self._seen_signatures.keys() - present:
                self._seen_signatures.pop(key, None)
                self._pending_paths.pop(key, None)

    def retry_pending(self):
        with self._launch_lock:
            if not self._pending_paths or time.monotonic() < self._next_retry:
                return
            self._pending_paths = {key: path for key, path in self._pending_paths.items() if path.is_file()}
            if self._pending_paths and self._maybe_launch_gui():
                self._pending_paths.clear()
            self._next_retry = time.monotonic() + LAUNCH_RETRY_SECONDS

    def on_created(self, event):
        if not event.is_directory:
            self._handle_document(Path(getattr(event, "src_path", "")))

    def on_moved(self, event):
        if not event.is_directory:
            # Also forget a path moved out of the inbox so a replacement with
            # the same size/timestamp is still treated as a new arrival.
            self.on_deleted(event)
            self._handle_document(Path(getattr(event, "dest_path", "")))

    def on_modified(self, event):
        if not event.is_directory:
            self._handle_document(Path(getattr(event, "src_path", "")))

    def on_deleted(self, event):
        if not event.is_directory:
            key = self._key(Path(getattr(event, "src_path", "")))
            with self._launch_lock:
                self._seen_signatures.pop(key, None)
                self._pending_paths.pop(key, None)


def _stop_observer(observer):
    if observer is not None:
        try:
            observer.stop()
            observer.join(timeout=3)
        except (OSError, RuntimeError):
            pass


def _observer_healthy(observer):
    return observer is not None and observer.is_alive() and all(
        emitter.is_alive() for emitter in observer.emitters
    )


def main():
    configure_logging()
    LOGGER.info("Starting helper: executable=%s, source=%s", sys.executable, SCRIPT_DIR)
    if not acquire_single_watcher_lock():
        return
    observer = None
    handler = None
    watch_folder = None
    next_settings_check = 0.0
    next_reconcile = 0.0
    next_observer_retry = 0.0
    last_problem = None
    try:
        while True:
            now = time.monotonic()
            if now >= next_settings_check:
                next_settings_check = now + SETTINGS_CHECK_SECONDS
                configure_logging()
                try:
                    configured_folder, _sorted_folder = load_folder_settings()
                    if configured_folder != watch_folder:
                        _stop_observer(observer)
                        observer = None
                        watch_folder = configured_folder
                        handler = LaunchOnDocumentEvent(watch_folder)
                        next_reconcile = next_observer_retry = 0.0
                        LOGGER.info("Watching folder: %s", watch_folder)
                    last_problem = None
                except WatchDisabledError as exc:
                    LOGGER.info("%s Stopping helper.", exc)
                    return
                except (OSError, ValueError) as exc:
                    if str(exc) != last_problem:
                        LOGGER.warning("Watch settings/folder unavailable; retrying: %s", exc)
                        last_problem = str(exc)

            if handler is not None:
                if now >= next_observer_retry and not _observer_healthy(observer):
                    _stop_observer(observer)
                    observer = Observer()
                    try:
                        observer.schedule(handler, str(watch_folder), recursive=False)
                        observer.start()
                        LOGGER.info("Native folder notifications started.")
                    except (OSError, RuntimeError) as exc:
                        _stop_observer(observer)
                        observer = None
                        LOGGER.warning("Native folder notifications unavailable; polling while retrying: %s", exc)
                    next_observer_retry = now + SETTINGS_CHECK_SECONDS
                if now >= next_reconcile:
                    next_reconcile = now + RECONCILE_SECONDS
                    try:
                        handler.reconcile()
                        handler.retry_pending()
                    except OSError as exc:
                        if str(exc) != last_problem:
                            LOGGER.warning("Folder scan unavailable; retrying: %s", exc)
                            last_problem = str(exc)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_observer(observer)
        release_single_watcher_lock()
        LOGGER.info("Watch and Launch helper stopped.")


if __name__ == "__main__":
    main()
