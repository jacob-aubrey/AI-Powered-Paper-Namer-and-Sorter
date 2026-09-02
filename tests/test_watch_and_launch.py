from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
            watch_and_launch.launch_gui()


if __name__ == "__main__":
    unittest.main()
