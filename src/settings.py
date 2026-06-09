import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppSettings:
    watch_folder: Path | None = None
    sorted_folder: Path | None = None
    api_key: str = ""
    naming_mode: str = "Automatic"

    def is_complete(self) -> bool:
        return bool(self.watch_folder and self.sorted_folder)

    def clean_naming_mode(self) -> str:
        return self.naming_mode if self.naming_mode in {"Automatic", "AI", "Basic"} else "Automatic"


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
                    watch_folder=Path(data["watch_folder"]).expanduser() if data.get("watch_folder") else None,
                    sorted_folder=Path(data["sorted_folder"]).expanduser() if data.get("sorted_folder") else None,
                    api_key=data.get("api_key", ""),
                    naming_mode=data.get("naming_mode", "Automatic"),
                )
            except Exception:
                continue
        return AppSettings()

    def save(self, settings: AppSettings):
        data = {
            "watch_folder": str(settings.watch_folder),
            "sorted_folder": str(settings.sorted_folder),
            "api_key": settings.api_key,
            "naming_mode": settings.clean_naming_mode(),
        }
        for path in (self.config_path, self.script_directory / "config.json"):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                self.config_path = path
                return
            except OSError:
                continue
        raise OSError("Could not save settings to AppData or the app folder.")
