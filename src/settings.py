import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppSettings:
    watch_folder: Path | None = None
    sorted_folder: Path | None = None

    def is_complete(self) -> bool:
        return bool(self.watch_folder and self.sorted_folder)


class SettingsManager:
    def __init__(self, script_directory: Path):
        self.script_directory = script_directory
        self.config_path = self._config_path()

    def _config_path(self) -> Path:
        local_config = self.script_directory / "config.json"
        if local_config.exists():
            return local_config
        appdata = Path.home() / "AppData" / "Roaming" / "AI Paper Sorter"
        return appdata / "config.json"

    def load(self) -> AppSettings:
        for path in (self.script_directory / "config.json", self.config_path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return AppSettings(
                    watch_folder=Path(data["watch_folder"]).expanduser(),
                    sorted_folder=Path(data["sorted_folder"]).expanduser(),
                )
            except Exception:
                continue
        return AppSettings()

    def save(self, settings: AppSettings):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "watch_folder": str(settings.watch_folder),
            "sorted_folder": str(settings.sorted_folder),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
