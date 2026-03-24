import os
from pathlib import Path
from typing import Optional


SOCCER_PLACE_DIRNAME = "Soccer_Place_Data"


def _safe_get(config, section: str, key: str, default=None):
    try:
        return config.get(section, key, default=default)
    except Exception:
        return default


def _norm_path(p: str) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def _is_legacy_soccer_scent_path(p: str) -> bool:
    """
    Legacy default paths used by older builds. We treat these as "unset" so the
    app defaults to Soccer_Place_Data on new machines.
    """
    if not p:
        return False
    n = _norm_path(p)
    return n.startswith("c:/soccerscent/")


def resolve_onedrive_dir() -> Optional[Path]:
    for env_var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = (os.getenv(env_var) or "").strip()
        if value:
            candidate = Path(value)
            if candidate.exists():
                return candidate

    fallback = Path.home() / "OneDrive"
    return fallback if fallback.exists() else None


def resolve_base_dir(config=None) -> Path:
    """
    Resolve the base folder for all user data (matches, logs, raw, leaguetables).

    Priority:
    1) env: SOCCER_PLACE_DATA_DIR or SOCCER_SCENT_HOME
    2) config: paths.base_dir
    3) OneDrive/<Soccer_Place_Data> if OneDrive exists
    4) ~/Soccer_Place_Data
    """
    for env_var in ("SOCCER_PLACE_DATA_DIR", "SOCCER_SCENT_HOME"):
        value = (os.getenv(env_var) or "").strip()
        if value:
            return Path(value).expanduser()

    configured = (_safe_get(config, "paths", "base_dir", default="") or "").strip()
    if configured and not _is_legacy_soccer_scent_path(configured):
        return Path(configured).expanduser()

    onedrive = resolve_onedrive_dir()
    if onedrive:
        return onedrive / SOCCER_PLACE_DIRNAME

    return Path.home() / SOCCER_PLACE_DIRNAME


def resolve_matches_dir(config=None) -> Path:
    configured = (
        _safe_get(config, "scraper", "default_export_path", default="") or ""
    ).strip()
    if configured and not _is_legacy_soccer_scent_path(configured):
        return Path(configured).expanduser()
    return resolve_base_dir(config) / "matches"


def resolve_logs_dir(config=None) -> Path:
    configured = (
        _safe_get(config, "scraper", "default_log_path", default="") or ""
    ).strip()
    if configured and not _is_legacy_soccer_scent_path(configured):
        return Path(configured).expanduser()
    return resolve_base_dir(config) / "data" / "logs"


def resolve_raw_dir(config=None) -> Path:
    configured = (os.getenv("SOCCER_SCENT_RAW_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return resolve_base_dir(config) / "data" / "raw"


def resolve_all_odds_dir(config=None) -> Path:
    return resolve_raw_dir(config) / "all_odds"


def resolve_processed_dir(config=None) -> Path:
    return resolve_base_dir(config) / "data" / "processed"


def resolve_leaguetables_dir(config=None) -> Path:
    return resolve_base_dir(config) / "leaguetables"


def resolve_leagues_dir(config=None) -> Path:
    return resolve_base_dir(config) / "data" / "leagues"


def resolve_league_board_path(config=None) -> Path:
    return resolve_leagues_dir(config) / "league_board.json"


def ensure_app_dirs(config=None) -> dict[str, Path]:
    dirs = {
        "base": resolve_base_dir(config),
        "matches": resolve_matches_dir(config),
        "logs": resolve_logs_dir(config),
        "raw": resolve_raw_dir(config),
        "processed": resolve_processed_dir(config),
        "leaguetables": resolve_leaguetables_dir(config),
        "leagues": resolve_leagues_dir(config),
    }

    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)

    return dirs
