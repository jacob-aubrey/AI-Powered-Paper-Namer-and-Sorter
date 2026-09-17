from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main
from version import APP_NAME, APP_VERSION, WINDOW_TITLE, windows_version_info


def _user32(**overrides):
    functions = {
        "FindWindowW": Mock(return_value=0),
        "EnumWindows": Mock(),
        "GetClassNameW": Mock(),
        "GetWindowTextLengthW": Mock(),
        "GetWindowTextW": Mock(),
        "IsIconic": Mock(return_value=False),
        "ShowWindow": Mock(),
        "SetForegroundWindow": Mock(),
    }
    functions.update(overrides)
    return SimpleNamespace(**functions)


class StartupTests(unittest.TestCase):
    def tearDown(self):
        main.gui_mutex_handle = None

    def test_new_gui_retains_full_width_mutex_until_shutdown(self):
        handle = 0x123456789
        kernel32 = SimpleNamespace(CreateMutexW=Mock(return_value=handle), CloseHandle=Mock())
        with (
            patch.object(main.os, "name", "nt"),
            patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(ctypes, "get_last_error", return_value=0, create=True),
        ):
            self.assertTrue(main.acquire_single_gui_lock())
            self.assertEqual(main.gui_mutex_handle, handle)
            self.assertIs(kernel32.CreateMutexW.restype, main.wintypes.HANDLE)
            kernel32.CloseHandle.assert_not_called()
            main.release_single_gui_lock()
        kernel32.CloseHandle.assert_called_once_with(handle)
        self.assertIsNone(main.gui_mutex_handle)

    def test_duplicate_gui_closes_only_its_duplicate_handle(self):
        kernel32 = SimpleNamespace(CreateMutexW=Mock(return_value=456), CloseHandle=Mock())
        with (
            patch.object(main.os, "name", "nt"),
            patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(ctypes, "get_last_error", return_value=183, create=True),
        ):
            self.assertFalse(main.acquire_single_gui_lock())
        kernel32.CloseHandle.assert_called_once_with(456)
        self.assertIsNone(main.gui_mutex_handle)

    def test_duplicate_launch_restores_minimized_existing_window(self):
        user32 = _user32(
            FindWindowW=Mock(return_value=123),
            IsIconic=Mock(return_value=True),
            ShowWindow=Mock(),
            SetForegroundWindow=Mock(),
        )
        with (
            patch.object(main.os, "name", "nt"),
            patch.object(ctypes, "WinDLL", return_value=user32, create=True),
        ):
            self.assertTrue(main.activate_existing_gui(timeout=0))
        user32.ShowWindow.assert_called_once_with(123, 9)
        user32.SetForegroundWindow.assert_called_once_with(123)

    def test_duplicate_launch_waits_for_starting_window(self):
        user32 = _user32(
            FindWindowW=Mock(side_effect=[0, 0, 123]),
            IsIconic=Mock(return_value=False),
            ShowWindow=Mock(),
            SetForegroundWindow=Mock(),
        )
        with (
            patch.object(main.os, "name", "nt"),
            patch.object(ctypes, "WinDLL", return_value=user32, create=True),
            patch.object(main.time, "sleep") as sleep,
        ):
            self.assertTrue(main.activate_existing_gui())
        sleep.assert_called_once_with(0.05)
        user32.ShowWindow.assert_called_once_with(123, 5)

    def test_shared_version_matches_window_title_and_executable_resource(self):
        self.assertEqual(WINDOW_TITLE, f"{APP_NAME} v{APP_VERSION}")
        self.assertEqual(main.GUI_WINDOW_TITLE, WINDOW_TITLE)
        resource = Path(__file__).resolve().parents[1] / "version_info.txt"
        self.assertEqual(resource.read_text(encoding="utf-8"), windows_version_info())

    def test_duplicate_launch_can_activate_legacy_unversioned_window(self):
        user32 = _user32(FindWindowW=Mock(side_effect=[0, 456]))
        with patch.object(main.os, "name", "nt"), patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            self.assertTrue(main.activate_existing_gui(timeout=0))
        self.assertEqual(
            [call.args for call in user32.FindWindowW.call_args_list],
            [("TkTopLevel", WINDOW_TITLE), ("TkTopLevel", APP_NAME)],
        )
        user32.SetForegroundWindow.assert_called_once_with(456)

    def test_duplicate_launch_activates_other_version_but_ignores_unrelated_windows(self):
        windows = {
            101: ("Notepad", "AI Paper Sorter v1.2.9"),
            102: ("TkTopLevel", "AI Paper Sorter v1.2.9 - notes"),
            103: ("TkTopLevel", "AI Paper Sorter v1.2.9"),
        }

        def enumerate_windows(callback, parameter):
            for window in windows:
                if not callback(window, parameter):
                    break
            return True

        def get_class(window, buffer, _length):
            buffer.value = windows[window][0]
            return len(buffer.value)

        def get_title(window, buffer, _length):
            buffer.value = windows[window][1]
            return len(buffer.value)

        user32 = _user32(
            EnumWindows=Mock(side_effect=enumerate_windows),
            GetClassNameW=Mock(side_effect=get_class),
            GetWindowTextLengthW=Mock(side_effect=lambda window: len(windows[window][1])),
            GetWindowTextW=Mock(side_effect=get_title),
        )
        with patch.object(main.os, "name", "nt"), patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            self.assertTrue(main.activate_existing_gui(timeout=0))
        user32.SetForegroundWindow.assert_called_once_with(103)

    def test_duplicate_launch_returns_false_when_no_app_window_exists(self):
        user32 = _user32()
        with patch.object(main.os, "name", "nt"), patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            self.assertFalse(main.activate_existing_gui(timeout=0))
        user32.SetForegroundWindow.assert_not_called()

    def test_duplicate_launch_activates_without_initializing_another_app(self):
        with (
            patch.object(sys, "argv", ["main.py"]),
            patch.object(main, "acquire_single_gui_lock", return_value=False),
            patch.object(main, "activate_existing_gui") as activate,
            patch.object(main, "release_single_gui_lock") as release,
        ):
            main.main()
        activate.assert_called_once_with()
        release.assert_not_called()

    def test_failed_app_startup_releases_lock(self):
        fake_app = SimpleNamespace(App=Mock(), DnDCTk=Mock(side_effect=RuntimeError("Tk unavailable")))
        with (
            patch.object(sys, "argv", ["main.py"]),
            patch.object(main, "acquire_single_gui_lock", return_value=True),
            patch.object(main, "release_single_gui_lock") as release,
            patch.dict(sys.modules, {"app": fake_app}),
        ):
            with self.assertRaisesRegex(RuntimeError, "Tk unavailable"):
                main.main()
        release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
