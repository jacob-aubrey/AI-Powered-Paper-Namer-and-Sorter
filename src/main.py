import sys
import os
import ctypes

from app import App, DnDCTk
from watch_and_launch import main as watch_main

gui_mutex_handle = None


def acquire_single_gui_lock() -> bool:
    global gui_mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gui_mutex_handle = kernel32.CreateMutexW(None, False, "Local\\LitSorterGuiMode")
    return ctypes.get_last_error() != 183


def main():
    if "--watch" in sys.argv:
        watch_main()
        return

    if not acquire_single_gui_lock():
        return

    root = DnDCTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
