"""
watch_and_launch.py
Continuously watches the configured To Sort folder.
Whenever a PDF is created, moved in, or modified, it launches the AI Paper Sorter GUI
(if it is not already running).
"""

import sys
import os
import time
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from settings import SettingsManager

# ----------------------------
# Environment & paths
# ----------------------------
def _script_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()

SCRIPT_DIR = _script_dir()

# Candidate GUI exe paths (both one-dir and one-file layouts supported)
GUI_EXE_CANDIDATES = [
    SCRIPT_DIR.parent / "AI Paper Sorter" / "AI Paper Sorter.exe",
    SCRIPT_DIR / "AI Paper Sorter.exe",  # one-file build in same dist
    Path(r"C:\Paper Sorter\dist\AI Paper Sorter.exe"),
]
GUI_PY = SCRIPT_DIR / "main.py"  # fallback (source)
GUI_PROCESS_NAMES = {"ai paper sorter.exe"}
launched_gui_process: subprocess.Popen | None = None

# ----------------------------
# Helpers
# ----------------------------
def find_gui_exe() -> Path | None:
    for cand in GUI_EXE_CANDIDATES:
        if cand.exists():
            return cand
    return None

def is_gui_running() -> bool:
    global launched_gui_process
    if launched_gui_process is not None and launched_gui_process.poll() is None:
        return True

    try:
        out = subprocess.check_output(
            ["tasklist"], creationflags=0x08000000
        ).decode(errors="ignore").lower()
        return any(process_name in out for process_name in GUI_PROCESS_NAMES)
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
    watch_folder = settings.watch_folder.expanduser().resolve()
    sorted_folder = settings.sorted_folder.expanduser().resolve()
    watch_folder.mkdir(parents=True, exist_ok=True)
    sorted_folder.mkdir(parents=True, exist_ok=True)
    return watch_folder, sorted_folder

def launch_gui():
    global launched_gui_process
    try:
        exe = find_gui_exe()
        if exe is not None:
            launched_gui_process = subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                creationflags=0x08000000,  # no console window
            )
        else:
            launched_gui_process = subprocess.Popen(
                [sys.executable, str(GUI_PY)],
                cwd=str(SCRIPT_DIR),
                creationflags=0x08000000,
            )
    except Exception as e:
        print("Launch error:", e)

# ----------------------------
# Watchdog handler
# ----------------------------
class LaunchOnPdfEvent(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self._last_launch_ts = 0.0
        self._DEBOUNCE_S = 5.0

    def _handle_pdf(self, path: Path):
        if path.suffix.lower() != ".pdf":
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
        if event.is_directory or p.suffix.lower() != ".pdf":
            return
        self._handle_pdf(p)

    def on_moved(self, event):
        dest = Path(getattr(event, "dest_path", ""))
        if dest and dest.suffix.lower() == ".pdf":
            self._handle_pdf(dest)

    def on_modified(self, event):
        p = Path(getattr(event, "src_path", ""))
        if event.is_directory or p.suffix.lower() != ".pdf":
            return
        self._handle_pdf(p)

# ----------------------------
# Main loop
# ----------------------------
def main():
    watch_folder, _sorted_folder = load_folder_settings()
    observer = Observer()
    handler = LaunchOnPdfEvent()
    observer.schedule(handler, str(watch_folder), recursive=False)
    observer.start()
    print(f"Watching {watch_folder} for new PDFs...")
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
