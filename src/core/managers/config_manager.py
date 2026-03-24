import json
import os
from pathlib import Path
from typing import Any

from core.config import CoreConfig


def _safe_print(message: str):
    text = str(message or "")
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


class ConfigManager:
    def __init__(self, config_path=None):
        self._core = CoreConfig(config_path)
        self._user_config_path = self._resolve_user_config_path()
        self._overrides: dict[str, Any] = self._load_user_overrides()
        self._apply_overrides()

    def _resolve_user_config_path(self) -> Path:
        configured = (os.getenv("SOCCER_SCENT_USER_CONFIG") or "").strip()
        if configured:
            return Path(configured).expanduser()

        for env_var in ("APPDATA", "LOCALAPPDATA"):
            root = (os.getenv(env_var) or "").strip()
            if root:
                return Path(root) / "SoccerScent" / "config.user.json"

        return Path.home() / ".soccer_scent" / "config.user.json"

    def _load_user_overrides(self) -> dict:
        path = self._user_config_path
        try:
            if not path.exists():
                return {}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            _safe_print(f"Failed to load user config overrides: {e}")
            return {}

    def _save_user_overrides(self) -> bool:
        path = self._user_config_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._overrides, f, indent=2, ensure_ascii=False, default=str)
            return True
        except Exception as e:
            _safe_print(f"Failed to save user config overrides: {e}")
            return False

    def _deep_merge(self, base: dict, override: dict) -> dict:
        merged = dict(base) if isinstance(base, dict) else {}
        if not isinstance(override, dict):
            return merged

        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _apply_overrides(self):
        try:
            self._core.data = self._deep_merge(self._core.data or {}, self._overrides or {})
        except Exception as e:
            _safe_print(f"Failed to apply user config overrides: {e}")

    def get(self, section: str, key: str, default=None):
        return self._core.get(section, key, default)

    def set(self, value, section: str, key: str):
        self._core.set(value, section, key)

        if not isinstance(section, str) or not isinstance(key, str):
            return
        if section not in self._overrides or not isinstance(self._overrides.get(section), dict):
            self._overrides[section] = {}
        self._overrides[section][key] = value

    def reload(self):
        self._core.data = self._core._load_config()
        self._overrides = self._load_user_overrides()
        self._apply_overrides()

    def save(self, updates: dict):
        try:
            for section_key, value in updates.items():
                if isinstance(section_key, tuple) and len(section_key) == 2:
                    self.set(value, section_key[0], section_key[1])
                elif isinstance(section_key, str):
                    self.set(value, section_key, "value")
            return self._save_user_overrides()
        except Exception as e:
            _safe_print(f"Config save failed: {e}")
            return False
