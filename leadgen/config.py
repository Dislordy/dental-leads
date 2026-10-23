"""Loads config.yaml (plus settings saved from the web app) and the .env file."""
import copy
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_SETTINGS = PROJECT_DIR / "settings.local.yaml"   # written by the web app's Settings page
ENV_PATH = PROJECT_DIR / ".env"
ENV_KEYS = ("GOOGLE_PLACES_API_KEY", "HUNTER_API_KEY")


class Config(dict):
    """config.yaml as a dict, plus resolved paths and API keys."""

    def path(self, key):
        p = Path(self[key])
        return p if p.is_absolute() else PROJECT_DIR / p

    @property
    def cache_path(self):
        p = Path(self["cache"]["path"])
        return p if p.is_absolute() else PROJECT_DIR / p


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(config_path=None, env_path=None, local_path=None):
    config_path = Path(config_path) if config_path else PROJECT_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    local_path = Path(local_path) if local_path else LOCAL_SETTINGS
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            data = deep_merge(data, yaml.safe_load(f) or {})
    cfg = Config(data)

    load_dotenv(env_path or ENV_PATH)
    cfg["google_api_key"] = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    cfg["hunter_api_key"] = os.getenv("HUNTER_API_KEY", "").strip()
    return cfg


def save_local_settings(overrides, local_path=None):
    """Merge overrides into settings.local.yaml (other values already in it are kept)."""
    path = Path(local_path) if local_path else LOCAL_SETTINGS
    if path.exists():
        with open(path, encoding="utf-8") as f:
            overrides = deep_merge(yaml.safe_load(f) or {}, overrides)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Saved by the web app's Settings page. Values here override config.yaml.\n")
        yaml.safe_dump(overrides, f, sort_keys=False, allow_unicode=True)


def save_env(values, env_path=None):
    """Set or clear keys in .env (other lines are kept) and in the running process."""
    path = Path(env_path) if env_path else ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    for k, v in values.items():
        os.environ[k] = v
