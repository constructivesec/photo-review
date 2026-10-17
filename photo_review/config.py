"""Persistent settings stored as JSON in the OS user config directory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "PhotoReview"


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "config.json"


@dataclass
class Config:
    last_folder: str = ""
    keep_folder: str = ""
    sort_key: str = "name_asc"
    window_geometry: str = ""  # base64 of QWidget.saveGeometry()
    show_gps: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        types = {f.name: type(f.default) for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in types and type(v) is types[k]})

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # settings are a convenience; never crash over them
