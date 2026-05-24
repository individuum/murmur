from __future__ import annotations

import json
import shutil
from typing import Any
from urllib.parse import urlparse

from paths import app_dir, bundled

DEFAULT_PATH = bundled("config.default.json")
USER_PATH = app_dir() / "config.json"


def _ensure_user_config() -> None:
    if not USER_PATH.exists():
        USER_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEFAULT_PATH, USER_PATH)


def _migrate(cfg: dict[str, Any]) -> dict[str, Any]:
    """Migrate older `server_url` keys into discrete host/port/scheme."""
    if "server_url" in cfg and "server_host" not in cfg:
        parsed = urlparse(cfg.pop("server_url"))
        cfg["server_scheme"] = parsed.scheme or "http"
        cfg["server_host"] = parsed.hostname or "127.0.0.1"
        cfg["server_port"] = parsed.port or (443 if parsed.scheme == "https" else 8000)
    return cfg


def load() -> dict[str, Any]:
    _ensure_user_config()
    with USER_PATH.open("r", encoding="utf-8") as fh:
        user = json.load(fh)
    with DEFAULT_PATH.open("r", encoding="utf-8") as fh:
        defaults = json.load(fh)
    merged = {**defaults, **_migrate(user)}
    return merged


def save(cfg: dict[str, Any]) -> None:
    with USER_PATH.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")


def server_url(cfg: dict[str, Any]) -> str:
    scheme = cfg.get("server_scheme", "http")
    host = cfg.get("server_host", "127.0.0.1")
    port = cfg.get("server_port", 8000)
    return f"{scheme}://{host}:{port}"
