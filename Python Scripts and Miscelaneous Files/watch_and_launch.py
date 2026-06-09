"""
watch_and_launch.py
Continuously watches the ToSort folder defined in config.json.
Whenever a new PDF is dropped in, it launches the AI Paper Sorter GUI
(if it is not already running).
"""

import sys
import os
import time
import json
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ----------------------------
# Environment & paths
# ----------------------------
def _script_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()

SCRIPT_DIR = _script_dir()

# Load configuration (must live next to this exe/script)
CONFIG_PATH = SCRIPT_DIR / "config.json"
if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"config.json not found at: {CONFIG_PATH}")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

WATCH_FOLDER = Path(config.get("watch_folder", "")).expanduser().resolve()
SORTED_FOLDER = Path(config.get("sorted_folder", "")).expanduser().resolve()

WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
SORTED_FOLDER.mkdir(parents=True, exist_ok=True)

# Candidate GUI exe paths (both one-dir and one-file layouts supported)
GUI_EXE_CANDIDATES = [
    SCRIPT_DIR.parent / "paper_sorter_gui" / "AI Paper Sorter.exe",  # one-dir build
    SCRIPT_DIR / "paper_sorter_gui" / "AI Paper Sorter.exe",
    SCRIPT_DIR / "AI Paper Sorter.exe",  # one-file build in same dist
    Path(r"C:\Paper Sorter\dist\paper_sorter_gui\AI Paper Sorter.exe"),
    Path(r"C:\Paper Sorter\dist\AI Paper Sorter.exe"),
]
GUI_PY = SCRIPT_DIR / "ai_paper_sorter.py"  # fallback (source)

# ----------------------------
# Helpers
# ----------------------------
def find_gui_exe() -> Path | None:
    for cand in GUI_EXE_CANDIDATES:
        if cand.exists():
            return cand
    return None

def is_gui_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist"], creationflags=0x08000000
        ).decode(errors="ignore").lower()
        return ("ai_paper_sorter.exe" in out) or (
            "python.exe" in out and "ai_paper_sorter.py" in out
        )
    except Exception:
        return False

def wait_until_stable(path: Path, max_wait: float = 60.0, sample_interval: float = 0.5) -> bool:
    deadline = time.time() + max_wait
    last_size = -1
    while time.time() < deadline:
        try:
            size = path.stat().st_size
            if size == last_size and size > 0:
                return True
            last_size = size
        except FileNotFoundError:
            pass
        time.sleep(sample_interval)
    return False

def launch_gui():
    try:
        exe = find_gui_exe()
        if exe is not None:
            subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                creationflags=0x08000000,  # no console window
            )
        else:
            subprocess.Popen(
                [sys.executable, str(GUI_PY)],
                cwd=str(SCRIPT_DIR),
                creationflags=0x08000000,
            )
    except Exception as e:
        print("Launch error:", e)

# ----------------------------
# Watchdog handler
# ----------------------------
class LaunchOnCreate(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self._last_launch_ts = 0.0
        self._DEBOUNCE_S = 5.0

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
        if wait_until_stable(p):
            self._maybe_launch_gui()

    def on_moved(self, event):
        dest = Path(getattr(event, "dest_path", ""))
        if dest and dest.suffix.lower() == ".pdf":
            if wait_until_stable(dest):
                self._maybe_launch_gui()

# ----------------------------
# Main loop
# ----------------------------
def main():
    observer = Observer()
    handler = LaunchOnCreate()
    observer.schedule(handler, str(WATCH_FOLDER), recursive=False)
    observer.start()
    print(f"Watching {WATCH_FOLDER} for new PDFs...")
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
