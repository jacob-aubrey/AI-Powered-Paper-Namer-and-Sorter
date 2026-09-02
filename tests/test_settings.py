from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from settings import AppSettings, SettingsManager  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_api_key_is_protected_in_per_user_config(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            appdata = root / "AppData"
            with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                manager = SettingsManager(root / "portable-copy")
                settings = AppSettings(
                    watch_folder=root / "incoming",
                    sorted_folder=root / "sorted",
                    api_key="test-key-not-for-network",
                    filename_format="journal_detailed",
                    custom_filename_template="{author_last}_{year}",
                    allow_cloud_ai_for_word_documents=True,
                )
                manager.save(settings)
                config_path = appdata / "AI Paper Sorter" / "config.json"
                serialized = json.loads(config_path.read_text(encoding="utf-8"))

                self.assertNotIn("api_key", serialized)
                self.assertIn("api_key_protected", serialized)
                self.assertTrue(serialized["allow_cloud_ai_for_word_documents"])
                self.assertEqual(serialized["filename_format"], "journal_detailed")
                loaded = manager.load()
                self.assertEqual(loaded.api_key, settings.api_key)
                self.assertEqual(loaded.filename_format, "journal_detailed")
                self.assertEqual(loaded.custom_filename_template, "{author_last}_{year}")
                self.assertTrue(loaded.allow_cloud_ai_for_word_documents)

    def test_save_removes_plaintext_key_from_a_legacy_portable_config(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            appdata = root / "AppData"
            portable = root / "portable-copy"
            portable.mkdir()
            legacy_config = portable / "config.json"
            legacy_config.write_text(
                json.dumps({"watch_folder": "old", "sorted_folder": "old", "api_key": "old-plain-key"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                manager = SettingsManager(portable)
                manager.save(AppSettings(watch_folder=root / "incoming", sorted_folder=root / "sorted", api_key="new-key"))

            self.assertNotIn("api_key", json.loads(legacy_config.read_text(encoding="utf-8")))

    def test_legacy_string_booleans_do_not_enable_optional_features(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            appdata = root / "AppData"
            config_path = appdata / "AI Paper Sorter" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "watch_folder": str(root / "incoming"),
                        "sorted_folder": str(root / "sorted"),
                        "watch_and_launch_enabled": "false",
                        "allow_cloud_ai_for_word_documents": "false",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                settings = SettingsManager(root / "portable-copy").load()

            self.assertFalse(settings.watch_and_launch_enabled)
            self.assertFalse(settings.allow_cloud_ai_for_word_documents)

    def test_relative_legacy_paths_are_stabilized_against_the_app_folder(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            appdata = root / "AppData"
            app_folder = root / "portable-copy"
            config_path = appdata / "AI Paper Sorter" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps({"watch_folder": "incoming", "sorted_folder": "sorted"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                settings = SettingsManager(app_folder).load()

            self.assertEqual(settings.watch_folder, (app_folder / "incoming").resolve())
            self.assertEqual(settings.sorted_folder, (app_folder / "sorted").resolve())

    def test_invalid_filename_format_loads_as_smart(self):
        settings = AppSettings(filename_format="not-a-real-format")

        self.assertEqual(settings.clean_filename_format(), "smart")


if __name__ == "__main__":
    unittest.main()
