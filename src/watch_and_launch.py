"""
Continuously watches the configured To Sort folder and opens AI Paper Sorter
as soon as a supported document appears. The GUI owns file-readiness checks.
"""

import sys
import os
import time
import subprocess
import ctypes
from ctypes import wintypes
import logging
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from document_types import is_processable_document
from settings import SettingsManager


# The background helper is packaged as a windowed executable. Do not fall back
# to stderr if it has no console attached.
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False


def _script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()


SCRIPT_DIR = _script_dir()
GUI_PY = SCRIPT_DIR / "main.py"
GUI_MUTEX_NAME = "Local\\LitSorterGuiMode"
launched_gui_process: subprocess.Popen | None = None
watcher_mutex_handle = None


def gui_launch_command() -> tuple[list[str], Path]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable))], Path(sys.executable).parent
    return [sys.executable, str(GUI_PY)], SCRIPT_DIR


def acquire_single_watcher_lock() -> bool:
    global watcher_mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    watcher_mutex_handle = kernel32.CreateMutexW(None, False, "Local\\LitSorterWatchMode")
    if not watcher_mutex_handle:
        LOGGER.error("Could not acquire the watcher lock: %s", ctypes.get_last_error())
        return False
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(watcher_mutex_handle)
        watcher_mutex_handle = None
        return False
    return True


def is_gui_running() -> bool:
    if launched_gui_process is not None and launched_gui_process.poll() is None:
        return True
    if os.name != "nt":
        return False

    # The GUI holds this mutex before importing UI dependencies. This detects
    # a GUI that is still starting, hidden, or minimized without PowerShell.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenMutexW(0x00100000, False, GUI_MUTEX_NAME)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # Access denied still means an instance owns the named object.
    return ctypes.get_last_error() == 5


def load_folder_settings():
    settings = SettingsManager(SCRIPT_DIR).load()
    if not settings.is_complete():
        raise FileNotFoundError("Folder settings are missing. Open AI Paper Sorter and choose folders in Settings first.")
    if not settings.watch_and_launch_enabled:
        raise FileNotFoundError("Watch and Launch is disabled in Settings.")
    watch_folder = settings.watch_folder.expanduser().resolve()
    sorted_folder = settings.sorted_folder.expanduser().resolve()
    watch_folder.mkdir(parents=True, exist_ok=True)
    sorted_folder.mkdir(parents=True, exist_ok=True)
    return watch_folder, sorted_folder


def launch_gui() -> bool:
    global launched_gui_process
    try:
        command, working_directory = gui_launch_command()
        launch_options = {}
        if getattr(sys, "frozen", False):
            # This is an independent app, not a PyInstaller worker subprocess.
            # It must not inherit the long-lived watcher's extraction state.
            launch_options["env"] = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        launched_gui_process = subprocess.Popen(
            command,
            cwd=str(working_directory),
            creationflags=0x08000000 if os.name == "nt" else 0,
            **launch_options,
        )
        return True
    except Exception as exc:
        LOGGER.error("Launch error: %s", exc)
        return False


class LaunchOnDocumentEvent(FileSystemEventHandler):
    def __init__(self, watch_folder: Path | None = None):
        super().__init__()
        self._watch_folder = watch_folder.resolve() if watch_folder is not None else None
        self._launch_lock = threading.Lock()

    def _handle_document(self, path: Path):
        if not is_processable_document(path):
            return
        try:
            if self._watch_folder is not None and path.resolve().parent != self._watch_folder:
                return
            if not path.is_file():
                return
        except OSError:
            return
        # Open immediately, including for an empty file still downloading. The
        # GUI waits for stable contents before extraction. Waiting here blocks
        # the watchdog dispatcher and delays all later documents behind it.
        self._maybe_launch_gui()

    def _maybe_launch_gui(self):
        # The initial scan and watchdog callbacks can run concurrently. The
        # child process and GUI mutex suppress duplicate launches without a
        # timer that could discard events immediately after the GUI closes.
        with self._launch_lock:
            if not is_gui_running():
                launch_gui()

    def on_created(self, event):
        if not event.is_directory:
            self._handle_document(Path(getattr(event, "src_path", "")))

    def on_moved(self, event):
        if not event.is_directory:
            self._handle_document(Path(getattr(event, "dest_path", "")))

    def on_modified(self, event):
        if not event.is_directory:
            self._handle_document(Path(getattr(event, "src_path", "")))


def main():
    if not acquire_single_watcher_lock():
        return
    try:
        watch_folder, _sorted_folder = load_folder_settings()
    except (FileNotFoundError, OSError) as exc:
        LOGGER.info("Watch and Launch did not start: %s", exc)
        return
    observer = Observer()
    handler = LaunchOnDocumentEvent(watch_folder)
    observer.schedule(handler, str(watch_folder), recursive=False)
    observer.start()
    LOGGER.info("Watching %s for supported documents...", watch_folder)
    try:
        # Cover files received before the login watcher finishes starting.
        # Watch first, then scan so additions cannot fall between the two.
        try:
            for path in watch_folder.iterdir():
                handler._handle_document(path)
        except OSError as exc:
            LOGGER.warning("Could not scan the watched folder: %s", exc)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
