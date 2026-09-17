from __future__ import annotations

import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import watch_and_launch  # noqa: E402
import main as app_entry  # noqa: E402
from settings import AppSettings  # noqa: E402


class _FakeObserver:
    def schedule(self, *_args, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    emitters = ()

    def is_alive(self):
        return True

    def join(self, **_kwargs):
        pass


class WatchAndLaunchTests(unittest.TestCase):
    def setUp(self):
        self.process_patch = patch.object(watch_and_launch, "launched_gui_process", None)
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)
        self.logging_patch = patch.object(watch_and_launch, "configure_logging")
        self.logging_patch.start()
        self.addCleanup(self.logging_patch.stop)
        self.activation_patch = patch.object(app_entry, "activate_existing_gui", return_value=True)
        self.activation = self.activation_patch.start()
        self.addCleanup(self.activation_patch.stop)

    def test_windowed_disabled_watcher_does_not_require_stdout(self):
        with (
            patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
            patch.object(watch_and_launch, "load_folder_settings", side_effect=watch_and_launch.WatchDisabledError("disabled")),
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
            self.assertEqual(launch.call_count, 1)

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


    def test_reconciliation_recovers_missed_event_without_reopening_unchanged_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            paper = inbox / "missed.pdf"
            handler = watch_and_launch.LaunchOnDocumentEvent(inbox)
            with (
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", return_value=True) as launch,
            ):
                handler.reconcile()
                paper.write_bytes(b"first")
                handler.reconcile()
                handler.reconcile()
                launch.assert_called_once()
                paper.write_bytes(b"changed document")
                handler.reconcile()
                self.assertEqual(launch.call_count, 2)
                paper.unlink()
                handler.reconcile()
                paper.write_bytes(b"new document")
                handler.reconcile()
                self.assertEqual(launch.call_count, 3)

    def test_failed_launch_is_retried_without_another_file_event(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "retry.pdf"
            paper.write_bytes(b"paper")
            handler = watch_and_launch.LaunchOnDocumentEvent(Path(directory))
            with (
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", side_effect=[False, True]) as launch,
                patch.object(watch_and_launch.time, "monotonic", return_value=10.0) as clock,
            ):
                handler._handle_document(paper)
                handler.retry_pending()
                launch.assert_called_once()
                clock.return_value = 16.0
                handler.retry_pending()
                self.assertEqual(launch.call_count, 2)
                self.assertFalse(handler._pending_paths)

    def test_new_document_restores_existing_gui_once(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "restore.pdf"
            paper.write_bytes(b"paper")
            handler = watch_and_launch.LaunchOnDocumentEvent(Path(directory))
            with patch.object(watch_and_launch, "is_gui_running", return_value=True):
                handler._handle_document(paper)
                handler.reconcile()
            self.activation.assert_called_once_with(timeout=0)

    def test_unavailable_login_folder_is_retried_then_watched(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            (inbox / "paper.pdf").write_bytes(b"paper")
            clock = [10.0]
            def advance(_seconds):
                clock[0] += 6.0
            with (
                patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
                patch.object(watch_and_launch, "release_single_watcher_lock") as release,
                patch.object(watch_and_launch, "load_folder_settings", side_effect=[
                    OSError("drive not ready"), (inbox, inbox / "sorted"),
                    watch_and_launch.WatchDisabledError("disabled"),
                ]) as load,
                patch.object(watch_and_launch, "Observer", _FakeObserver),
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", return_value=True) as launch,
                patch.object(watch_and_launch.time, "sleep", side_effect=advance),
                patch.object(watch_and_launch.time, "monotonic", side_effect=lambda: clock[0]),
            ):
                watch_and_launch.main()
            self.assertEqual(load.call_count, 3)
            launch.assert_called_once()
            release.assert_called_once()

    def test_observer_failure_still_scans_for_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            (inbox / "paper.pdf").write_bytes(b"paper")
            observer = Mock()
            observer.start.side_effect = OSError("native notifications unavailable")
            with (
                patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
                patch.object(watch_and_launch, "load_folder_settings", return_value=(inbox, inbox / "sorted")),
                patch.object(watch_and_launch, "Observer", return_value=observer),
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", return_value=True) as launch,
                patch.object(watch_and_launch.time, "sleep", side_effect=KeyboardInterrupt),
            ):
                watch_and_launch.main()
            launch.assert_called_once()

    def test_runtime_folder_change_replaces_observer_and_reads_new_inbox(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "first", Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            (second / "paper.pdf").write_bytes(b"paper")
            observers = [Mock(emitters=()), Mock(emitters=())]
            clock = [10.0]
            def advance(_seconds):
                clock[0] += 6.0
            with (
                patch.object(watch_and_launch, "acquire_single_watcher_lock", return_value=True),
                patch.object(watch_and_launch, "load_folder_settings", side_effect=[
                    (first, first / "sorted"), (second, second / "sorted"),
                    watch_and_launch.WatchDisabledError("disabled"),
                ]),
                patch.object(watch_and_launch, "Observer", side_effect=observers),
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", return_value=True) as launch,
                patch.object(watch_and_launch.time, "sleep", side_effect=advance),
                patch.object(watch_and_launch.time, "monotonic", side_effect=lambda: clock[0]),
            ):
                watch_and_launch.main()
            observers[0].stop.assert_called_once()
            observers[1].schedule.assert_called_once()
            self.assertEqual(observers[1].schedule.call_args.args[1], str(second))
            launch.assert_called_once()

    def test_output_drive_does_not_block_inbox_watcher(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "inbox"
            output = Path(directory) / "unavailable-output"
            settings = AppSettings(watch_folder=inbox, sorted_folder=output, watch_and_launch_enabled=True)
            with patch.object(watch_and_launch.SettingsManager, "load", return_value=settings):
                result = watch_and_launch.load_folder_settings()
            self.assertEqual(result, (inbox.resolve(), output.resolve()))
            self.assertTrue(inbox.is_dir())
            self.assertFalse(output.exists())

    def test_real_filesystem_creation_and_download_rename_reach_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            received = threading.Event()
            handler = watch_and_launch.LaunchOnDocumentEvent(inbox)
            observer = watch_and_launch.Observer()
            observer.schedule(handler, str(inbox), recursive=False)
            with (
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", side_effect=lambda: received.set() or True),
            ):
                observer.start()
                try:
                    (inbox / "created.pdf").touch()
                    self.assertTrue(received.wait(3), "native creation event was missed")
                    received.clear()
                    partial = inbox / "download.pdf.crdownload"
                    partial.write_bytes(b"paper")
                    partial.rename(inbox / "download.pdf")
                    self.assertTrue(received.wait(3), "native download rename event was missed")
                finally:
                    observer.stop()
                    observer.join(timeout=3)


    def test_failed_launch_event_burst_obeys_retry_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "retry.pdf"
            handler = watch_and_launch.LaunchOnDocumentEvent(Path(directory))
            with (
                patch.object(watch_and_launch, "is_gui_running", return_value=False),
                patch.object(watch_and_launch, "launch_gui", return_value=False) as launch,
                patch.object(watch_and_launch.time, "monotonic", return_value=10.0),
            ):
                for size in range(5):
                    paper.write_bytes(b"x" * size)
                    handler._handle_document(paper)
            launch.assert_called_once()
            self.assertEqual(handler._next_retry, 15.0)

    @unittest.skipUnless(os.name == "nt", "Windows named-mutex integration")
    def test_running_status_tracks_real_session_mutex_without_launching(self):
        mutex_name = "Local\\LitSorterWatcherTest-" + uuid.uuid4().hex
        kernel32 = watch_and_launch._kernel32()
        with (
            patch.object(watch_and_launch, "WATCHER_MUTEX_NAME", mutex_name),
            patch.object(watch_and_launch.subprocess, "Popen") as popen,
        ):
            self.assertFalse(watch_and_launch.is_watcher_running())
            handle = kernel32.CreateMutexW(None, False, mutex_name)
            self.assertTrue(handle)
            try:
                self.assertTrue(watch_and_launch.is_watcher_running())
            finally:
                kernel32.CloseHandle(handle)
            self.assertFalse(watch_and_launch.is_watcher_running())
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
