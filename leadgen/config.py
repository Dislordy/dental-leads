"""Loads config.yaml and the .env file."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent


class Config(dict):
    """config.yaml as a dict, plus resolved paths and API keys."""

    def path(self, key):
        p = Path(self[key])
        return p if p.is_absolute() else PROJECT_DIR / p

    @property
    def cache_path(self):
        p = Path(self["cache"]["path"])
        return p if p.is_absolute() else PROJECT_DIR / p


def load_config(config_path=None, env_path=None):
    config_path = Path(config_path) if config_path else PROJECT_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = Config(yaml.safe_load(f))

    load_dotenv(env_path or PROJECT_DIR / ".env")
    cfg["google_api_key"] = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    cfg["hunter_api_key"] = os.getenv("HUNTER_API_KEY", "").strip()
    return cfg
