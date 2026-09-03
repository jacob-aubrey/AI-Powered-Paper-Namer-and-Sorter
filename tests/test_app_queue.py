from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from app import App, TextboxRedirector  # noqa: E402


class _FakeTextbox:
    def __init__(self):
        self.state = "normal"
        self.tag_bindings = {}
        self.widget_bindings = {}
        self.tags_at_click = ()
        self.inserted = []
        self.deleted = []

    def tag_config(self, *_args, **_kwargs):
        pass

    def tag_bind(self, tag, event, callback):
        self.tag_bindings[(tag, event)] = callback

    def bind(self, event, callback, add=True):
        self.widget_bindings[event] = callback

    def index(self, _coordinate):
        return "1.0"

    def tag_names(self, _index):
        return self.tags_at_click

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def cget(self, key):
        return self.state if key == "state" else None

    def after(self, _delay, callback, *args):
        callback(*args)

    def insert(self, _index, text, tags=()):
        self.inserted.append((text, tags))

    def delete(self, start, end):
        self.deleted.append((start, end))

    def see(self, _index):
        pass


class _FakeRoot:
    def __init__(self):
        self.scheduled = []

    def grab_current(self):
        return None

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))
        return f"after-{len(self.scheduled)}"


class QueueCoalescingTests(unittest.TestCase):
    def test_move_log_pattern_accepts_apostrophes_in_destination_names(self):
        line = "MOVED: source.docx -> C:\\Library\\O'Connor\\report.docx"
        match = TextboxRedirector.MOVED_PATH_RE.search(line)

        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "C:\\Library\\O'Connor\\report.docx")

    def test_snoozed_unchanged_file_is_not_requeued_but_refresh_can_retry_it(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            document_path = Path(temporary_directory) / "incoming.docx"
            document = Document()
            document.add_paragraph("A document waiting for review")
            document.save(document_path)

            app = App.__new__(App)
            app.queue_lock = threading.Lock()
            app.queued_sort_paths = set()
            app.snoozed_sort_signatures = {}
            app.ignored_watch_event_until = {}
            app.file_queue = Queue()

            app._snooze_sort_file(document_path)
            app._queue_sort_file(document_path)
            self.assertTrue(app.file_queue.empty())

            app._queue_sort_file(document_path, force=True)
            self.assertFalse(app.file_queue.empty())
            self.assertEqual(app.file_queue.get_nowait(), document_path)

    def test_self_generated_rename_event_is_suppressed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            document_path = Path(temporary_directory) / "renamed.docx"
            document_path.write_bytes(b"placeholder")
            app = App.__new__(App)
            app.queue_lock = threading.Lock()
            app.queued_sort_paths = set()
            app.snoozed_sort_signatures = {}
            app.ignored_watch_event_until = {}
            app.file_queue = Queue()

            app._suppress_watch_events_for(document_path)
            app._queue_sort_file(document_path)

            self.assertTrue(app.file_queue.empty())

    def test_watcher_ignores_moves_outside_the_to_sort_folder(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            incoming = root / "incoming"
            incoming.mkdir()
            app = App.__new__(App)
            app.WATCH_FOLDER = incoming
            queued = []
            app._queue_sort_file = queued.append
            handler = app.create_watchdog_handler()

            handler.on_moved(SimpleNamespace(is_directory=False, dest_path=str(root / "library" / "paper.pdf")))
            self.assertEqual(queued, [])
            handler.on_moved(SimpleNamespace(is_directory=False, dest_path=str(incoming / "paper.pdf")))
            self.assertEqual(queued, [incoming / "paper.pdf"])

    def test_active_modal_leaves_the_next_gui_item_queued(self):
        app = App.__new__(App)
        app.root = _FakeRoot()
        app.gui_queue = Queue()
        app.gui_queue.put(("sort", Path("C:/incoming/paper.pdf"), {}))
        app.gui_modal_depth = 1
        app.gui_queue_after_id = None

        app.process_gui_queue()

        self.assertEqual(app.gui_queue.qsize(), 1)
        self.assertEqual(len(app.root.scheduled), 1)


class SmartSettingsRoutingTests(unittest.TestCase):
    @staticmethod
    def _settings(*, mode="Automatic", word_ai=False, presentation_ai=False, online_lookup=True):
        return SimpleNamespace(
            api_key="test-key",
            clean_naming_mode=lambda: mode,
            allow_cloud_ai_for_word_documents=word_ai,
            allow_cloud_ai_for_presentation_documents=presentation_ai,
            online_metadata_lookup_enabled=online_lookup,
        )

    def test_word_ai_opt_out_keeps_doi_lookup_available(self):
        app = App.__new__(App)
        app.settings = self._settings(word_ai=False, online_lookup=True)
        app.ENV_API_KEY = ""
        expected = {"source": "DOI"}

        with patch("app.get_document_details", return_value=expected) as get_details:
            result = app._get_details_for_document(Path("C:/incoming/report.docx"))

        self.assertIs(result, expected)
        self.assertEqual(get_details.call_args.kwargs["allow_cloud_ai"], False)
        self.assertEqual(get_details.call_args.kwargs["allow_online_metadata_lookup"], True)

    def test_powerpoint_ai_setting_is_passed_only_for_pptx(self):
        app = App.__new__(App)
        app.settings = self._settings(presentation_ai=True, online_lookup=True)
        app.ENV_API_KEY = ""

        with patch("app.get_document_details", return_value={"source": "Basic"}) as get_details:
            app._get_details_for_document(Path("C:/incoming/slides.pptx"))
            app._get_details_for_document(Path("C:/incoming/old-slides.ppt"))

        self.assertTrue(get_details.call_args_list[0].kwargs["allow_cloud_ai"])
        self.assertFalse(get_details.call_args_list[1].kwargs["allow_cloud_ai"])
        self.assertTrue(get_details.call_args_list[0].kwargs["allow_online_metadata_lookup"])

    def test_local_only_mode_never_calls_the_smart_lookup(self):
        app = App.__new__(App)
        app.settings = self._settings(mode="Basic")
        app.ENV_API_KEY = ""
        expected = {"source": "Basic"}

        with patch("app.get_basic_document_details", return_value=expected) as get_basic, patch(
            "app.get_document_details"
        ) as get_details:
            result = app._get_details_for_document(Path("C:/incoming/private.pdf"))

        self.assertIs(result, expected)
        get_basic.assert_called_once()
        get_details.assert_not_called()


class LogDisplayTests(unittest.TestCase):
    def test_read_only_log_preserves_clickable_document_link_bindings(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            document_path = Path(temporary_directory) / "report.docx"
            document_path.write_bytes(b"placeholder")
            textbox = _FakeTextbox()
            redirector = TextboxRedirector(textbox)

            redirector.write(f"MOVED: source.docx -> {document_path}\n")

            self.assertEqual(textbox.state, "disabled")
            self.assertIn("<Button-1>", textbox.widget_bindings)
            # The first appended link is View Location; the second is View Document.
            textbox.tags_at_click = ("log_link", "log_link_2")
            with patch("app.os.startfile") as startfile:
                result = textbox.widget_bindings["<Button-1>"](SimpleNamespace(x=0, y=0))
            self.assertEqual(result, "break")
            startfile.assert_called_once_with(str(document_path), "open")

    def test_log_location_link_uses_explorer_and_highlights_existing_document(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            document_path = Path(temporary_directory) / "report.pdf"
            document_path.write_bytes(b"placeholder")
            redirector = TextboxRedirector(_FakeTextbox())

            with patch("app.subprocess.Popen") as popen:
                redirector._open_location(document_path)

            popen.assert_called_once_with(
                ["explorer.exe", f"/select,{document_path}"], shell=False
            )

    def test_stale_log_link_uses_one_safe_matching_document(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            replacement = root / "New Category" / "report.pdf"
            replacement.parent.mkdir()
            replacement.write_bytes(b"placeholder")
            stale_path = root / "Old Category" / "report.pdf"
            redirector = TextboxRedirector(
                _FakeTextbox(), resolve_document_path=lambda _path: replacement
            )

            with patch("app.os.startfile") as startfile:
                redirector._open_paper(stale_path)

            startfile.assert_called_once_with(str(replacement), "open")

    def test_missing_log_link_reports_a_clear_error_without_launching(self):
        reported = []
        redirector = TextboxRedirector(
            _FakeTextbox(), on_link_error=lambda *args: reported.append(args)
        )

        with patch("app.os.startfile") as startfile, patch("app.logging.error"):
            redirector._open_paper(Path("C:/missing/report.pdf"))

        startfile.assert_not_called()
        self.assertEqual(reported[0][0], "open document")
        self.assertIsInstance(reported[0][2], FileNotFoundError)

    def test_ambiguous_stale_log_link_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "A").mkdir()
            (root / "B").mkdir()
            (root / "A" / "same-name.pdf").write_bytes(b"a")
            (root / "B" / "same-name.pdf").write_bytes(b"b")
            app = App.__new__(App)
            app.SORTED_FOLDER = root

            self.assertIsNone(app._resolve_logged_document_path(root / "Old" / "same-name.pdf"))

    def test_clear_display_does_not_reenable_text_editing(self):
        textbox = _FakeTextbox()
        redirector = TextboxRedirector(textbox)

        redirector.clear_display()

        self.assertEqual(textbox.deleted, [("1.0", "end")])
        self.assertEqual(textbox.state, "disabled")
        self.assertEqual(redirector._link_callbacks, {})


if __name__ == "__main__":
    unittest.main()
