"""
watch_and_launch.py
Continuously watches the configured To Sort folder.
Whenever a supported document is created, moved in, or modified, it launches the AI Paper Sorter GUI
(if it is not already running).
"""

import sys
import os
import time
import subprocess
import ctypes
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from document_types import is_processable_document
from settings import SettingsManager


# The background helper is packaged as a windowed executable.  Do not fall back
# to stderr if it has no console attached.
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False

# ----------------------------
# Environment & paths
# ----------------------------
def _script_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()

SCRIPT_DIR = _script_dir()

GUI_PY = SCRIPT_DIR / "main.py"  # source fallback
GUI_WINDOW_TITLE = "AI Paper Sorter"
launched_gui_process: subprocess.Popen | None = None
watcher_mutex_handle = None

# ----------------------------
# Helpers
# ----------------------------
def gui_launch_command() -> tuple[list[str], Path]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable))], Path(sys.executable).parent
    return [sys.executable, str(GUI_PY)], SCRIPT_DIR

def acquire_single_watcher_lock() -> bool:
    global watcher_mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    watcher_mutex_handle = kernel32.CreateMutexW(None, False, "Local\\LitSorterWatchMode")
    return ctypes.get_last_error() != 183

def is_gui_running() -> bool:
    global launched_gui_process
    if launched_gui_process is not None and launched_gui_process.poll() is None:
        return True

    try:
        powershell = (
            "Get-Process | "
            f"Where-Object {{ $_.MainWindowTitle -eq '{GUI_WINDOW_TITLE}' }} | "
            "Select-Object -First 1"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", powershell],
            creationflags=0x08000000,
        ).decode(errors="ignore")
        return bool(out.strip())
    except Exception:
        return False

def wait_until_stable(path: Path, max_wait: float = 60.0, sample_interval: float = 0.5, stable_for: float = 2.0) -> bool:
    deadline = time.time() + max_wait
    last_signature = None
    stable_since = None
    while time.time() < deadline:
        try:
            stat = path.stat()
            if stat.st_size > 0:
                with open(path, "rb"):
                    pass
                signature = (stat.st_size, stat.st_mtime_ns)
                if signature == last_signature:
                    stable_since = stable_since or time.time()
                    if time.time() - stable_since >= stable_for:
                        return True
                else:
                    last_signature = signature
                    stable_since = time.time()
            else:
                last_signature = None
                stable_since = None
        except OSError:
            last_signature = None
            stable_since = None
            pass
        time.sleep(sample_interval)
    return False

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

def launch_gui():
    global launched_gui_process
    try:
        command, working_directory = gui_launch_command()
        launched_gui_process = subprocess.Popen(
            command,
            cwd=str(working_directory),
            creationflags=0x08000000,
        )
    except Exception as e:
        # A PyInstaller windowed executable does not necessarily have stdout.
        LOGGER.error("Launch error: %s", e)

# ----------------------------
# Watchdog handler
# ----------------------------
class LaunchOnDocumentEvent(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self._last_launch_ts = 0.0
        self._DEBOUNCE_S = 5.0

    def _handle_document(self, path: Path):
        if not is_processable_document(path):
            return
        if wait_until_stable(path):
            self._maybe_launch_gui()

    def _maybe_launch_gui(self):
        now = time.time()
        if (now - self._last_launch_ts) < self._DEBOUNCE_S:
            return
        if not is_gui_running():
            launch_gui()
        self._last_launch_ts = now

    def on_created(self, event):
        p = Path(getattr(event, "src_path", ""))
        if event.is_directory or not is_processable_document(p):
            return
        self._handle_document(p)

    def on_moved(self, event):
        dest = Path(getattr(event, "dest_path", ""))
        if dest and is_processable_document(dest):
            self._handle_document(dest)

    def on_modified(self, event):
        p = Path(getattr(event, "src_path", ""))
        if event.is_directory or not is_processable_document(p):
            return
        self._handle_document(p)

# ----------------------------
# Main loop
# ----------------------------
def main():
    if not acquire_single_watcher_lock():
        return
    try:
        watch_folder, _sorted_folder = load_folder_settings()
    except (FileNotFoundError, OSError) as exc:
        # The packaged application is windowed, so sys.stdout may be None here.
        LOGGER.info("Watch and Launch did not start: %s", exc)
        return
    observer = Observer()
    handler = LaunchOnDocumentEvent()
    observer.schedule(handler, str(watch_folder), recursive=False)
    observer.start()
    LOGGER.info("Watching %s for supported documents...", watch_folder)
    try:
        while True:
            time.sleep(1)  # keep running forever
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()
