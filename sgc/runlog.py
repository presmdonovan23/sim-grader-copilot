"""Run folder, manifest, and run log (technical spec section 12)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import REPORT_PREFIX, RUN_DIR_PREFIX, TIMESTAMP_FORMAT
from .errors import FatalError

LOGGER_NAME = "sgc"


def submission_id(index: int) -> str:
    """Opaque ID for the Nth submission (1-based): S001, S002, ..."""
    return f"S{index:03d}"


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


class RunFolder:
    """The `sgc_run_<timestamp>/` folder next to the report, inside the submissions directory."""

    def __init__(self, base_dir: Path, started: Optional[datetime] = None) -> None:
        self.base_dir = base_dir
        self.started = started or datetime.now()
        stamp = self.started.strftime(TIMESTAMP_FORMAT)
        suffix = ""
        n = 1
        while (
            (base_dir / f"{RUN_DIR_PREFIX}{stamp}{suffix}").exists()
            or (base_dir / f"{REPORT_PREFIX}{stamp}{suffix}.pdf").exists()
        ):
            n += 1
            suffix = f"_{n}"
        self.stamp = f"{stamp}{suffix}"
        self.run_dir = base_dir / f"{RUN_DIR_PREFIX}{self.stamp}"
        self.report_path = base_dir / f"{REPORT_PREFIX}{self.stamp}.pdf"
        self._handler: Optional[logging.Handler] = None

    def create(self) -> None:
        try:
            self.run_dir.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            raise FatalError(f"Cannot create run folder {self.run_dir}: {exc.strerror or exc}") from exc
        handler = logging.FileHandler(self.run_dir / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self._handler = handler

    def close(self) -> None:
        if self._handler is not None:
            logging.getLogger(LOGGER_NAME).removeHandler(self._handler)
            self._handler.close()
            self._handler = None

    def submission_dir(self, sid: str) -> Path:
        path = self.run_dir / sid
        path.mkdir(exist_ok=True)
        return path

    def write_manifest(self, manifest: dict) -> None:
        write_json(self.run_dir / "manifest.json", manifest)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
