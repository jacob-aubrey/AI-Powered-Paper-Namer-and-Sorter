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
        self.inserted = []
        self.deleted = []

    def tag_config(self, *_args, **_kwargs):
        pass

    def tag_bind(self, tag, event, callback):
        self.tag_bindings[(tag, event)] = callback

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


class LogDisplayTests(unittest.TestCase):
    def test_read_only_log_preserves_clickable_document_link_bindings(self):
        textbox = _FakeTextbox()
        redirector = TextboxRedirector(textbox)

        redirector.write("MOVED: source.docx -> C:\\Library\\report.docx\n")

        self.assertEqual(textbox.state, "disabled")
        self.assertIn(("log_link_1", "<Button-1>"), textbox.tag_bindings)
        with patch("app.os.startfile") as startfile:
            textbox.tag_bindings[("log_link_1", "<Button-1>")](None)
        startfile.assert_called_once_with(str(Path("C:\\Library\\report.docx").parent))

    def test_clear_display_does_not_reenable_text_editing(self):
        textbox = _FakeTextbox()
        redirector = TextboxRedirector(textbox)

        redirector.clear_display()

        self.assertEqual(textbox.deleted, [("1.0", "end")])
        self.assertEqual(textbox.state, "disabled")


if __name__ == "__main__":
    unittest.main()
