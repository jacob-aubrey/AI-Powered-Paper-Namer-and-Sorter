import sys
import os
import ctypes
import time
import re
from ctypes import wintypes

from version import APP_NAME, WINDOW_TITLE

gui_mutex_handle = None
GUI_MUTEX_NAME = "Local\\LitSorterGuiMode"
GUI_WINDOW_TITLE = WINDOW_TITLE
GUI_WINDOW_CLASS = "TkTopLevel"
_VERSIONED_WINDOW_TITLE = re.compile(re.escape(APP_NAME) + r" v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
_WINDOW_ENUM_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # HANDLE is pointer-sized: ctypes' default integer return type truncates it
    # on 64-bit Windows and prevents reliable handle cleanup.
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def acquire_single_gui_lock() -> bool:
    global gui_mutex_handle
    if os.name != "nt":
        return True
    kernel32 = _kernel32()
    handle = kernel32.CreateMutexW(None, False, GUI_MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS, including a GUI still starting.
        kernel32.CloseHandle(handle)
        return False
    gui_mutex_handle = handle
    return True


def release_single_gui_lock():
    global gui_mutex_handle
    if os.name == "nt" and gui_mutex_handle is not None:
        _kernel32().CloseHandle(gui_mutex_handle)
        gui_mutex_handle = None


def _find_existing_gui_window(user32):
    # Keep compatibility with an already-running release whose title did not
    # contain a version. Restrict all matches to Tk application windows.
    for title in (GUI_WINDOW_TITLE, APP_NAME):
        window = user32.FindWindowW(GUI_WINDOW_CLASS, title)
        if window:
            return window

    matches = []

    @_WINDOW_ENUM_CALLBACK
    def check_window(window, _parameter):
        class_name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(window, class_name, len(class_name))
        if class_name.value != GUI_WINDOW_CLASS:
            return True
        length = user32.GetWindowTextLengthW(window)
        if not 0 < length <= 200:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, title, len(title))
        if _VERSIONED_WINDOW_TITLE.fullmatch(title.value):
            matches.append(window)
            return False
        return True

    user32.EnumWindows(check_window, 0)
    return matches[0] if matches else None


def activate_existing_gui(timeout: float = 5.0) -> bool:
    """Restore an existing window, allowing a just-started GUI time to appear."""
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.EnumWindows.argtypes = [_WINDOW_ENUM_CALLBACK, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        window = _find_existing_gui_window(user32)
        if window:
            user32.ShowWindow(window, 9 if user32.IsIconic(window) else 5)
            user32.SetForegroundWindow(window)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from diagnostics import run_self_test
        raise SystemExit(run_self_test(sys.argv[2]))

    if "--watch" in sys.argv:
        from watch_and_launch import main as watch_main
        watch_main()
        return

    if not acquire_single_gui_lock():
        activate_existing_gui()
        return

    try:
        from app import App, DnDCTk

        root = DnDCTk()
        App(root)
        root.mainloop()
    finally:
        release_single_gui_lock()


if __name__ == "__main__":
    main()
