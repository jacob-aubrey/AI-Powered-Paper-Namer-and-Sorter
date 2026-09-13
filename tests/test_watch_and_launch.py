from __future__ import annotations

import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import watch_and_launch  # noqa: E402


class _FakeObserver:
    def schedule(self, *_args, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def join(self):
        pass


class WatchAndLaunchTests(unittest.TestCase):
    def setUp(self):
        self.process_patch = patch.object(watch_and_launch, "launched_gui_process", None)
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)

    def test_windowed_disabled_watcher_does_not_require_stdout(self):
        with (
            patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
            patch.object(watch_and_launch, "load_folder_settings", side_effect=FileNotFoundError("disabled")),
            patch.object(sys, "stdout", None),
        ):
            watch_and_launch.main()

    def test_windowed_enabled_watcher_does_not_require_stdout(self):
        with (
            patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
            patch.object(watch_and_launch, "load_folder_settings", return_value=(Path("C:/incoming"), Path("C:/sorted"))),
            patch.object(watch_and_launch, "Observer", _FakeObserver),
            patch.object(watch_and_launch.time, "sleep", side_effect=KeyboardInterrupt),
            patch.object(sys, "stdout", None),
        ):
            watch_and_launch.main()

    def test_windowed_failed_launch_does_not_require_stderr(self):
        with (
            patch.object(watch_and_launch, "gui_launch_command", return_value=(["not-a-real-command"], Path.cwd())),
            patch.object(watch_and_launch.subprocess, "Popen", side_effect=OSError("failed")),
            patch.object(sys, "stderr", None),
        ):
            self.assertFalse(watch_and_launch.launch_gui())

    def test_incomplete_download_opens_gui_without_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "new.pdf"
            paper.touch()
            handler = watch_and_launch.LaunchOnDocumentEvent(Path(directory))
            with (
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui") as launch,
                patch.object(watch_and_launch.time, "sleep", side_effect=AssertionError("Watcher blocked for readiness")),
            ):
                handler.on_created(SimpleNamespace(src_path=str(paper), is_directory=False))
            launch.assert_called_once()

    def test_download_rename_and_modification_events_open_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "download.pdf"
            paper.write_bytes(b"complete")
            handler = watch_and_launch.LaunchOnDocumentEvent(Path(directory))
            with (
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui") as launch,
            ):
                handler.on_moved(SimpleNamespace(src_path=str(paper) + ".crdownload", dest_path=str(paper), is_directory=False))
                handler.on_modified(SimpleNamespace(src_path=str(paper), is_directory=False))
            self.assertEqual(launch.call_count, 2)

    def test_unsupported_missing_directory_and_outgoing_move_do_not_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            inbox = base / "inbox"
            inbox.mkdir()
            outgoing = base / "outside.pdf"
            outgoing.write_bytes(b"pdf")
            nested = inbox / "nested"
            nested.mkdir()
            nested_paper = nested / "nested.pdf"
            nested_paper.write_bytes(b"pdf")
            lock_file = inbox / "~$Draft.docx"
            lock_file.write_bytes(b"lock")
            partial_file = inbox / "download.pdf.crdownload"
            partial_file.write_bytes(b"download")
            pdf_directory = inbox / "folder.pdf"
            pdf_directory.mkdir()
            handler = watch_and_launch.LaunchOnDocumentEvent(inbox)
            with patch.object(watch_and_launch, "launch_gui") as launch:
                handler.on_moved(SimpleNamespace(dest_path=str(outgoing), is_directory=False))
                handler.on_moved(SimpleNamespace(dest_path=str(pdf_directory), is_directory=True))
                for path in (nested_paper, lock_file, partial_file, inbox / "missing.pdf", pdf_directory):
                    handler.on_created(SimpleNamespace(src_path=str(path), is_directory=False))
            launch.assert_not_called()

    def test_event_burst_and_startup_scan_share_one_launch(self):
        process = Mock()
        process.poll.return_value = None
        kernel32 = Mock()
        kernel32.OpenMutexW.return_value = None
        handler = watch_and_launch.LaunchOnDocumentEvent()
        with (
            patch.object(watch_and_launch.ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(watch_and_launch.ctypes, "get_last_error", return_value=2, create=True),
            patch.object(watch_and_launch.subprocess, "Popen", return_value=process) as popen,
        ):
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda _: handler._maybe_launch_gui(), range(20)))
        popen.assert_called_once()

    def test_new_event_immediately_after_gui_closes_can_reopen(self):
        process = Mock()
        process.poll.return_value = None
        kernel32 = Mock()
        kernel32.OpenMutexW.return_value = None
        handler = watch_and_launch.LaunchOnDocumentEvent()
        with (
            patch.object(watch_and_launch.ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(watch_and_launch.ctypes, "get_last_error", return_value=2, create=True),
            patch.object(watch_and_launch.subprocess, "Popen", return_value=process) as popen,
        ):
            handler._maybe_launch_gui()
            process.poll.return_value = 0
            handler._maybe_launch_gui()
        self.assertEqual(popen.call_count, 2)

    def test_existing_gui_mutex_detects_gui_before_window_exists(self):
        kernel32 = Mock()
        kernel32.OpenMutexW.return_value = 0x123456789
        with (
            patch.object(watch_and_launch.os, "name", "nt"),
            patch.object(watch_and_launch.ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(watch_and_launch.subprocess, "Popen") as popen,
        ):
            watch_and_launch.LaunchOnDocumentEvent()._maybe_launch_gui()
        kernel32.CloseHandle.assert_called_once_with(0x123456789)
        popen.assert_not_called()

    def test_watcher_lock_failure_and_duplicate_do_not_start_an_observer(self):
        kernel32 = Mock()
        for handle, error in ((None, 5), (1234, 183)):
            with self.subTest(handle=handle, error=error):
                kernel32.CreateMutexW.return_value = handle
                with (
                    patch.object(watch_and_launch.os, "name", "nt"),
                    patch.object(watch_and_launch.ctypes, "WinDLL", return_value=kernel32, create=True),
                    patch.object(watch_and_launch.ctypes, "get_last_error", return_value=error, create=True),
                    patch.object(watch_and_launch, "watcher_mutex_handle", None),
                    patch.object(watch_and_launch, "Observer") as observer,
                ):
                    watch_and_launch.main()
                observer.assert_not_called()
        kernel32.CloseHandle.assert_called_once_with(1234)

    def test_frozen_launch_has_independent_pyinstaller_environment(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(watch_and_launch, "gui_launch_command", return_value=(["AI Paper Sorter.exe"], Path.cwd())),
            patch.object(watch_and_launch.subprocess, "Popen") as popen,
        ):
            self.assertTrue(watch_and_launch.launch_gui())
        self.assertEqual(popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_startup_scans_existing_documents_after_observer_is_started(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            (inbox / "missed-during-login.pdf").write_bytes(b"pdf")
            (inbox / "ignore.txt").write_text("not a document")
            calls = []
            observer = Mock()
            observer.start.side_effect = lambda: calls.append("watch")
            with (
                patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
                patch.object(watch_and_launch, "load_folder_settings", return_value=(inbox, inbox / "sorted")),
                patch.object(watch_and_launch, "Observer", return_value=observer),
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", side_effect=lambda: calls.append("launch")),
                patch.object(watch_and_launch.time, "sleep", side_effect=KeyboardInterrupt),
            ):
                watch_and_launch.main()
            self.assertEqual(calls, ["watch", "launch"])
            observer.stop.assert_called_once()
            observer.join.assert_called_once()


if __name__ == "__main__":
    unittest.main()
