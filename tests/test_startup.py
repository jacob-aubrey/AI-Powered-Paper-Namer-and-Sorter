from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main


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
        user32 = SimpleNamespace(
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
        user32 = SimpleNamespace(
            FindWindowW=Mock(side_effect=[0, 123]),
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
