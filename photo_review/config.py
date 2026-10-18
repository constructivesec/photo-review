"""Persistent settings: JSON in the OS user config directory, with a schema_version key."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from platformdirs import user_config_dir

from . import export, tag_logic

APP_NAME = "PhotoReview"
SCHEMA_VERSION = 1
SORT_KEYS = ("name_asc", "name_desc", "date", "modified_asc", "modified_desc", "rating")

log = logging.getLogger(__name__)


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    schema_version: int = SCHEMA_VERSION
    last_folder: str = ""
    sort_key: str = "name_asc"
    show_gps: bool = False
    tag_history: list[str] = field(default_factory=list)
    mirror_legacy: bool = True
    set_create_date: bool = False
    export_sequence_prefix: bool = True
    caption_bar_percent: int = export.BAR_PERCENT_DEFAULT
    first_launch_notice_dismissed: bool = False
    window_geometry: str = ""  # base64 QWidget.saveGeometry()

    def validated(self) -> Config:
        if self.sort_key not in SORT_KEYS:
            self.sort_key = "name_asc"
        self.caption_bar_percent = max(
            export.BAR_PERCENT_MIN, min(export.BAR_PERCENT_MAX, int(self.caption_bar_percent))
        )
        self.tag_history = [t for t in self.tag_history if isinstance(t, str)][: tag_logic.HISTORY_MAX]
        self.schema_version = SCHEMA_VERSION
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load settings. A corrupt file is renamed to .bad and defaults are used."""
        path = path or config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            types = {f.name: f.type for f in fields(cls)}
            kwargs = {}
            defaults = cls()
            for k, v in data.items():
                if k not in types:
                    continue
                if type(v) is not type(getattr(defaults, k)):
                    raise ValueError(f"{k} has the wrong type")
                kwargs[k] = v
            return cls(**kwargs).validated()
        except (OSError, ValueError, TypeError) as exc:
            bad = path.with_suffix(".bad")
            log.warning("Config %s is corrupt (%s); renamed to %s", path, exc, bad.name)
            try:
                os.replace(path, bad)
            except OSError:
                log.exception("Could not rename corrupt config")
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.validated()), indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("Could not save settings to %s", path)
