from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app import App, TextboxRedirector, center_window_over_master
from tests.test_app_queue import _FakeTextbox


class StopWorker(BaseException):
    pass


class AppReliabilityTests(unittest.TestCase):
    def test_sort_worker_continues_after_unexpected_document_error(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "broken.pdf"
            second = Path(directory) / "next.pdf"
            first.touch()
            second.touch()
            app = App.__new__(App)
            app.file_queue = Mock()
            app.file_queue.get.side_effect = [first, second, StopWorker()]
            app.gui_queue = Queue()
            app._wait_for_file_ready = Mock(return_value=True)
            app._get_details_for_document = Mock(side_effect=[RuntimeError("bad document"), {"title": "Next"}])
            app._clear_queued_sort_file = Mock()
            with patch("app.logging.exception"), self.assertRaises(StopWorker):
                app.processing_loop()
            self.assertEqual(app.gui_queue.get_nowait(), ("sort", second, {"title": "Next"}))
            app._clear_queued_sort_file.assert_called_once_with(first)
            self.assertEqual(app.file_queue.task_done.call_count, 2)

    def test_rename_worker_reports_failure_and_continues(self):
        first, second = Path("broken.pdf"), Path("next.pdf")
        app = App.__new__(App)
        app.rename_queue = Mock()
        app.rename_queue.get.side_effect = [first, second, StopWorker()]
        app.gui_queue = Queue()
        app._get_details_for_document = Mock(side_effect=[RuntimeError("bad document"), {"title": "Next"}])
        with patch("app.logging.exception"), self.assertRaises(StopWorker):
            app.rename_processing_loop()
        self.assertEqual(app.gui_queue.get_nowait(), ("rename_failed", first, {}))
        self.assertEqual(app.gui_queue.get_nowait(), ("rename", second, {"title": "Next"}))
        self.assertEqual(app.rename_queue.task_done.call_count, 2)

    def test_stale_location_matches_brackets_literally(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder / "Category" / "paper[1].pdf"
            target.parent.mkdir()
            target.touch()
            app = App.__new__(App)
            app.SORTED_FOLDER = folder
            self.assertEqual(app._resolve_logged_document_path(folder / "Old" / target.name), target)

    def test_short_generic_title_cannot_verify_a_longer_doi_title(self):
        from core_logic import DocumentExtraction, _verify_doi_matches_document
        from metadata_lookup import DOIResolution
        record = DOIResolution(doi="10.1234/test", provider="Crossref", evidence_label="retrieved", title="Physics of Unrelated Materials")
        extraction = DocumentExtraction(text="Physics", metadata={"title": "Physics"})
        self.assertFalse(_verify_doi_matches_document(record, extraction)[0])

    def test_windows_device_prefix_is_reserved_even_before_extra_dots(self):
        from core_logic import validate_document_filename
        for filename in ("CON.report.pdf", "nul.backup.docx", "COM1.notes.pdf"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                validate_document_filename(filename, Path(filename).suffix)
        self.assertEqual(validate_document_filename("concept.report.pdf", ".pdf"), "concept.report.pdf")

    def test_registered_watcher_upgrade_only_accepts_our_watch_action(self):
        app = App.__new__(App)
        app._startup_file_path = Mock(return_value=None)
        for arguments, command, expected in (
            ("--watch", "C:/Old Version/AI Paper Sorter.exe", {"C:/Old Version/AI Paper Sorter.exe"}),
            ("--other", "C:/Old Version/AI Paper Sorter.exe", set()),
            ("--watch", "C:/Other App/Other.exe", set()),
        ):
            with self.subTest(arguments=arguments, command=command):
                xml = f'<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Actions><Exec><Command>{command}</Command><Arguments>{arguments}</Arguments></Exec></Actions></Task>'
                app._run_hidden = Mock(return_value=SimpleNamespace(returncode=0, stdout=xml))
                self.assertEqual(app._registered_watch_launcher_markers(), expected)

    def test_stopping_registered_helper_matches_exact_paths(self):
        app = App.__new__(App)
        app._registered_watch_launcher_markers = Mock(return_value={"C:/Old [Version]/AI Paper Sorter.exe"})
        app._watch_launcher_process_marker = Mock(return_value="C:/New Version/AI Paper Sorter.exe")
        app._run_hidden = Mock()
        app._stop_watch_launcher_process()
        script = app._run_hidden.call_args.args[0][-1]
        self.assertIn("$_.ExecutablePath -eq 'C:/Old [Version]/AI Paper Sorter.exe'", script)
        self.assertIn("$_.ExecutablePath -eq 'C:/New Version/AI Paper Sorter.exe'", script)
        self.assertIn("--watch", script)
        self.assertNotIn("-like", script)

    def test_frozen_helper_is_independent_of_gui_lifetime(self):
        app = App.__new__(App)
        app._watch_launcher_command = Mock(return_value=("sorter.exe", "--watch", "C:/App"))
        app._watch_launcher_popen_args = Mock(return_value=["sorter.exe", "--watch"])
        with patch.object(sys, "frozen", True, create=True), patch("app.subprocess.Popen") as launch:
            app._start_watch_launcher_now()
        self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_worker_logging_never_calls_tk_from_background_thread(self):
        textbox = _FakeTextbox()
        redirector = TextboxRedirector(textbox)
        thread = threading.Thread(target=redirector.write, args=("Worker message\n",))
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(textbox.inserted, [])
        redirector.drain_pending()
        self.assertIn(("Worker message", ()), textbox.inserted)
        self.assertEqual(textbox.state, "disabled")

    def test_centering_does_not_apply_display_scaling_twice(self):
        window = SimpleNamespace(
            update_idletasks=lambda: None,
            winfo_vrootx=lambda: 0, winfo_vrooty=lambda: 0,
            winfo_vrootwidth=lambda: 1920, winfo_vrootheight=lambda: 1080,
            winfo_width=lambda: 1025, winfo_reqwidth=lambda: 1025,
            winfo_height=lambda: 812, winfo_reqheight=lambda: 812,
            _apply_window_scaling=lambda value: value * 1.25,
            _reverse_window_scaling=lambda value: value / 1.25,
            geometry=Mock(), lift=lambda: None, focus_force=lambda: None,
        )
        master = SimpleNamespace(
            update_idletasks=lambda: None, winfo_width=lambda: 1100,
            winfo_reqwidth=lambda: 1100, winfo_height=lambda: 900,
            winfo_reqheight=lambda: 900, winfo_rootx=lambda: 100,
            winfo_rooty=lambda: 40,
        )
        with patch("app._dialog_work_area", return_value=(0, 0, 1920, 1080)):
            center_window_over_master(window, master, min_width=700, min_height=560)
        self.assertTrue(window.geometry.call_args.args[0].startswith("820x649+"))

    def _confirmation_app(self, folder):
        app = App.__new__(App)
        app.root = Mock()
        app.SORTED_FOLDER = folder
        app._proposed_filename = Mock(return_value="report.pdf")
        app._wait_for_dialog = Mock()
        app._normalize_root = Mock()
        app._snooze_sort_file = Mock()
        app.choose_destination_folder = Mock(return_value=folder)
        app._messagebox = Mock(return_value=SimpleNamespace(get=lambda: None))
        return app

    def test_dismissed_final_confirmation_never_moves_document(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            app = self._confirmation_app(folder)
            source = folder / "incoming.pdf"
            source.write_bytes(b"original")
            with patch("app.FilenameEditorDialog", return_value=SimpleNamespace(result="report.pdf")), patch("app.shutil.move") as move:
                app.handle_user_confirmation_sort(source, {})
            move.assert_not_called()
            app._snooze_sort_file.assert_called_once_with(source)
            self.assertEqual(source.read_bytes(), b"original")

    def test_dismissed_duplicate_confirmation_never_proceeds(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "report.pdf").touch()
            app = self._confirmation_app(folder)
            source = folder / "incoming.pdf"
            with patch("app.FilenameEditorDialog", return_value=SimpleNamespace(result="report.pdf")), patch("app.shutil.move") as move:
                app.handle_user_confirmation_sort(source, {})
            app.choose_destination_folder.assert_not_called()
            move.assert_not_called()
            app._snooze_sort_file.assert_called_once_with(source)


if __name__ == "__main__":
    unittest.main()
