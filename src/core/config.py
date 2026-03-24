import json
import os
import sys
from pathlib import Path
from typing import Optional


def _safe_print(message: str):
    text = str(message or "")
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


class CoreConfig:
    def __init__(self, config_path: Optional[str] = None):
        self._path = str(config_path) if config_path else self._resolve_default_path()
        self.data = self._load_config()

    def _resolve_default_path(self) -> str:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return str(Path(meipass) / "assets" / "config.json")

        here = Path(__file__).resolve()
        src_dir = here.parent.parent
        candidate = src_dir / "assets" / "config.json"
        if candidate.exists():
            return str(candidate)

        return str(Path(os.path.abspath(".")) / "assets" / "config.json")

    def _load_config(self):
        if not os.path.exists(self._path):
            _safe_print(f"Config file not found at {self._path}. Using empty config.")
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _safe_print(f"Failed to load config: {e}")
            return {}

    def get(self, section: str, key: str, default=None):
        if not isinstance(section, str) or not isinstance(key, str):
            return default
        section_data = self.data.get(section, {})
        if not isinstance(section_data, dict):
            return default
        return section_data.get(key, default)

    def set(self, value, section: str, key: str):
        if not isinstance(section, str) or not isinstance(key, str):
            return
        if section not in self.data or not isinstance(self.data[section], dict):
            self.data[section] = {}
        self.data[section][key] = value

    def save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            _safe_print(f"Failed to save config: {e}")

    def reload(self):
        self.data = self._load_config()
