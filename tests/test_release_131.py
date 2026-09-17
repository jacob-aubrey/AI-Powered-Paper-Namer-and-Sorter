import json
import sys
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core_logic as core
from app import App
from settings import AppSettings


class CitationTests(unittest.TestCase):
    def test_surname_counts_and_particles(self):
        cases = [
            (["Jane Smith"], "Smith"),
            (["Jane Smith", "Jacob Aubrey"], "Smith_and_Aubrey_et_al"),
            (["Jane Smith", "Jacob Aubrey", "Jo Doe"], "Smith_et_al"),
            (["Smith, Jane", "Aubrey, Jacob"], "Smith_and_Aubrey_et_al"),
            (["Jonathan De Roo"], "De_Roo"),
            (["Smith J. A."], "Smith"),
        ]
        for authors, prefix in cases:
            with self.subTest(authors=authors):
                d = core.normalize_document_details({"creators": authors, "journal": "Journal", "year": "2026", "document_type": "journal_article"}, "paper.pdf")
                self.assertEqual(core.build_proposed_filename(d, ".pdf"), prefix + "_Journal_2026.pdf")
                d["is_supplementary_material"] = True
                self.assertEqual(core.build_proposed_filename(d, ".pdf"), prefix + "_Journal_2026_SI.pdf")

    def extraction(self, complete=False):
        return core.DocumentExtraction(text="Supporting Information\nA Long Technical Title\nAuthors: Jane Smith and Jacob Aubrey\n" + ("Journal: Journal\nYear: 2026\n" if complete else ""), metadata={}, document_format="PDF")

    def test_complete_si_uses_local_fields_without_ai(self):
        with patch.object(core, "extract_document", return_value=self.extraction(True)), patch.object(core, "_load_genai") as load:
            d = core.get_document_details("supp.pdf", "unused", allow_cloud_ai=True, allow_online_metadata_lookup=False)
        load.assert_not_called()
        self.assertEqual(core.build_proposed_filename(d, ".pdf"), "Smith_and_Aubrey_et_al_Journal_2026_SI.pdf")
        self.assertEqual(d["missing_citation_fields"], [])

    def test_incomplete_si_uses_ai_only_for_missing_fields(self):
        self.assertTrue(core._load_genai())
        client = MagicMock()
        client.models.generate_content.return_value.text = json.dumps({"creators": ["Wrong Name"], "journal": "Journal", "year": "2026", "document_type": "journal_article"})
        with patch.object(core, "extract_document", return_value=self.extraction()), patch.object(core.genai, "Client", return_value=client):
            d = core.get_document_details("supp.pdf", "test-key", allow_cloud_ai=True, allow_online_metadata_lookup=False)
        client.models.generate_content.assert_called_once()
        client.close.assert_called_once()
        self.assertEqual(core.build_proposed_filename(d, ".pdf"), "Smith_and_Aubrey_et_al_Journal_2026_SI.pdf")
        self.assertEqual(d["missing_citation_fields"], [])

    def test_ai_disallowed_keeps_missing_fields_explicit(self):
        with patch.object(core, "extract_document", return_value=self.extraction()), patch.object(core, "_load_genai") as load:
            d = core.get_document_details("supp.docx", "test-key", allow_cloud_ai=False, allow_online_metadata_lookup=False)
        load.assert_not_called()
        self.assertEqual(core.build_proposed_filename(d, ".docx"), "Smith_and_Aubrey_et_al_UnknownJournal_UnknownYear_SI.docx")
        self.assertTrue(d["needs_review"])
        self.assertEqual(d["missing_citation_fields"], ["journal", "year"])

    def test_wrapped_si_byline_has_six_people_and_no_creation_year(self):
        text = ("Supporting Information:\nAn Example Study of Carefully Prepared\nMaterials with a Long Wrapped\nTitle\n"
                "Morgan James Smith, Taylor Alex Aubrey, Alex Example,\n"
                "Jordan Robin Lee Doe, Harry Brown, and Jonathan De Roo\u2217\nDepartment of Chemistry\nContents\nReferences 2020")
        extraction = core.DocumentExtraction(text=text, metadata={"creation_date": "D:20260101"})
        with patch.object(core, "extract_document", return_value=extraction):
            d = core.get_basic_document_details("supp.pdf")
        self.assertEqual(len(d["creators"]), 6)
        self.assertEqual(d["creators"][0], "Morgan James Smith")
        self.assertEqual(d["creators"][-1], "Jonathan De Roo")
        self.assertEqual(d["year"], "Unknown")
        self.assertEqual(core.build_proposed_filename(d, ".pdf"), "Smith_et_al_UnknownJournal_UnknownYear_SI.pdf")


class WatchControlsTests(unittest.TestCase):
    def app(self):
        a = App.__new__(App)
        a.settings = AppSettings(watch_folder=Path("Incoming"), sorted_folder=Path("Library"), api_key="private", filename_format="custom", custom_filename_template="{year}_{title}", watch_and_launch_enabled=True)
        a.settings_manager = Mock()
        a.observer = Mock()
        a._watch_launch_sync_lock = threading.Lock()
        a._sync_watch_launch_setting = Mock()
        return a

    def test_stop_changes_only_saved_flag_and_keeps_open_app_watching(self):
        a = self.app()
        before = asdict(a.settings)
        with patch("watch_and_launch.is_watcher_running", return_value=True), patch("app.threading.Thread") as thread:
            a._request_watch_launch_action("stop")
            thread.call_args.kwargs["target"]()
        expected = dict(before, watch_and_launch_enabled=False)
        self.assertEqual(asdict(a.settings), expected)
        a.settings_manager.save.assert_called_once_with(a.settings)
        a.observer.stop.assert_not_called()
        a._sync_watch_launch_setting.assert_called_once()
        self.assertFalse(a._watch_launch_operation_pending)

    def test_failed_save_does_not_stop_watcher_or_change_memory(self):
        a = self.app()
        a.settings_manager.save.side_effect = OSError("read only")
        with patch("watch_and_launch.is_watcher_running", return_value=True), self.assertRaises(OSError):
            a._request_watch_launch_action("stop")
        self.assertTrue(a.settings.watch_and_launch_enabled)
        a._sync_watch_launch_setting.assert_not_called()

    def test_busy_controls_do_not_race_with_registration(self):
        a = self.app()
        a._watch_launch_sync_lock.acquire()
        with patch("watch_and_launch.is_watcher_running", return_value=True), self.assertRaisesRegex(ValueError, "still updating"):
            a._request_watch_launch_action("restart")
        a.settings_manager.save.assert_not_called()

    def test_operation_failure_is_visible_in_status(self):
        a = self.app()
        a._sync_watch_launch_setting.side_effect = OSError("startup denied")
        with patch("watch_and_launch.is_watcher_running", return_value=False), patch("app.threading.Thread") as thread, patch("app.logging.exception"):
            a._request_watch_launch_action("start")
            thread.call_args.kwargs["target"]()
            self.assertIn("startup denied", a._background_watch_status()["message"])
            self.assertFalse(a._background_watch_status()["busy"])
