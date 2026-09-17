import json
import sys
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core_logic as core
from app import FilenameEditorDialog, SettingsDialog, App
from settings import AppSettings

class ServiceError(Exception):
    def __init__(self, code):
        self.code = code

class RecoveryTests(unittest.TestCase):
    def metadata(self, errors):
        core._load_genai()
        client = MagicMock()
        client.models.generate_content.side_effect = errors
        extraction = core.DocumentExtraction(text="Supporting Information\nA Long Example Article Title\nAuthors: Jane Smith\n", metadata={}, document_format="PDF")
        with patch.object(core, "extract_document", return_value=extraction), patch.object(core.genai, "Client", return_value=client), patch.object(core.time, "sleep") as sleep:
            details = core.get_document_details("supp.pdf", "fake", allow_cloud_ai=True, allow_online_metadata_lookup=False)
        return details, client, sleep

    def test_overload_retries_once_and_recovers(self):
        response = Mock(text=json.dumps({"journal":"Journal", "year":"2026", "document_type":"journal_article"}))
        d, client, sleep = self.metadata([ServiceError(503), response])
        self.assertEqual(client.models.generate_content.call_count, 2)
        sleep.assert_called_once_with(1.0)
        self.assertEqual(core.build_proposed_filename(d, ".pdf"), "Smith_Journal_2026_SI.pdf")
        self.assertFalse(d.get("ai_retry_available",False))
        client.close.assert_called_once()

    def test_persistent_overload_is_explained_and_bounded(self):
        d, client, sleep = self.metadata([ServiceError(503)] * 3)
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertTrue(d["ai_retry_available"])
        self.assertIn("AI temporarily unavailable",d["review_reasons"][0])
        self.assertIn("journal, year",d["review_reasons"][0])
        self.assertEqual(d["document_type_label"],"Supporting Information")
        client.close.assert_called_once()

    def test_invalid_key_and_quota_are_not_automatically_retried(self):
        for code in (400,401,403,429):
            with self.subTest(code=code):
                d, client, sleep = self.metadata([ServiceError(code)])
                client.models.generate_content.assert_called_once()
                sleep.assert_not_called()
                self.assertTrue(d["ai_retry_available"])

    def test_title_joins_opening_lines_and_stops_before_authors_and_contents(self):
        text="Supporting Information:\nMechanism-Guided Preparation of Early\nTransition Metals to Access Novel Oxo\nClusters\nJane Smith, John Doe, Alex Brown\nDepartment of Chemistry\nContents\n3.3 TGA of clusters . . . . . . . S-23"
        self.assertEqual(core._title_from_text(text), "Mechanism-Guided Preparation of Early Transition Metals to Access Novel Oxo Clusters")
        self.assertEqual(core._title_from_text("Quantum Materials\nJane Smith\nAbstract"), "Quantum Materials")
        self.assertEqual(core._title_from_text("A Long Example Article Title\nAuthors: Jane Smith\nJournal: Example"), "A Long Example Article Title")
        self.assertEqual(core._title_from_text("Contents\n3.3 Long contents item . . . S-23"), "")

    def dialog(self, entry):
        d=FilenameEditorDialog.__new__(FilenameEditorDialog)
        d._retry_results=Queue()
        d._retry_original_entry=entry
        d._last_proposed_name="old.pdf"
        d.filename_entry=Mock()
        d.continue_button=Mock()
        d.retry_button=Mock()
        d.retry_status=Mock()
        d._render_details=Mock()
        d._retry_results.put(({"source":"AI"},"new.pdf"))
        # The worker packages its result and exception separately.
        payload=d._retry_results.get_nowait()
        d._retry_results.put((payload,None))
        return d

    def test_retry_preserves_manually_edited_filename(self):
        d=self.dialog("my_edit.pdf")
        d._poll_ai_retry()
        d.filename_entry.delete.assert_not_called()
        self.assertIn("edited filename was kept", d.retry_status.configure.call_args.kwargs["text"])

    def test_retry_updates_unedited_proposal(self):
        d=self.dialog("old.pdf")
        d._poll_ai_retry()
        d.filename_entry.insert.assert_called_once_with(0,"new.pdf")
        self.assertFalse(d._retry_busy)

    def test_manual_retry_still_uses_current_local_only_preference(self):
        a=App.__new__(App);a.settings=AppSettings(naming_mode="Basic");a.ENV_API_KEY="unused"
        with patch("app.get_basic_document_details", return_value={"title":"Local"}) as local, patch("app.get_document_details") as online:
            a._retry_document_proposal(Path("example.pdf"))
        local.assert_called_once()
        online.assert_not_called()

    def test_start_button_becomes_restart_only_when_running(self):
        d=SettingsDialog.__new__(SettingsDialog)
        d._watcher_action=Mock();d.watch_launch_var=Mock();d._messagebox=Mock()
        for running, expected in ((False,"start"),(True,"restart")):
            d._watcher_status=lambda: {"running":running}
            d._run_watcher_action("start")
            d._watcher_action.assert_called_with(expected)

    def test_registered_versioned_executable_is_recognized(self):
        a=App.__new__(App);a._startup_file_path=Mock(return_value=None)
        xml='<Task><Actions><Exec><Command>C:/Apps/AI Paper Sorter v1.3.2.exe</Command><Arguments>--watch</Arguments></Exec></Actions></Task>'
        a._run_hidden=Mock(return_value=Mock(returncode=0,stdout=xml))
        self.assertEqual(a._registered_watch_launcher_markers(),{"C:/Apps/AI Paper Sorter v1.3.2.exe"})
