import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _safe_print(message: str):
    text = str(message or "")
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "ignore").decode("ascii"))


def load_env_from_assets():
    meipass = getattr(sys, "_MEIPASS", None)

    if meipass:
        env_path = Path(meipass) / "assets" / ".env"
    else:
        here = Path(__file__).resolve()
        src_dir = here.parent.parent  # .../src
        candidate = src_dir / "assets" / ".env"
        env_path = (
            candidate
            if candidate.exists()
            else Path(os.path.abspath(".")) / "assets" / ".env"
        )

    if env_path.exists():
        load_dotenv(dotenv_path=str(env_path))
        _safe_print(f"Loaded .env from: {env_path}")
    else:
        _safe_print(f".env file not found at: {env_path}")
