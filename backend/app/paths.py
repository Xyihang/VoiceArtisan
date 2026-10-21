"""运行期目录布局（全部位于配置指定的数据盘）。"""
from __future__ import annotations

from pathlib import Path

from .config import AppConfig


class Paths:
    def __init__(self, cfg: AppConfig) -> None:
        self.root: Path = cfg.data_dir
        self.db = self.root / "db"
        self.db_file = self.db / "app.db"
        self.uploads = self.root / "uploads"
        self.outputs = self.root / "outputs"
        self.voices = self.root / "voices"
        self.logs = self.root / "logs"
        self.certs = self.root / "certs"
        self.tmp = self.root / "tmp"
        self.assets = self.root / "assets"

    def ensure(self) -> "Paths":
        for p in (
            self.root,
            self.db,
            self.uploads,
            self.outputs,
            self.voices,
            self.logs,
            self.certs,
            self.tmp,
            self.assets,
        ):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def task_dir(self, task_id: str) -> Path:
        d = self.outputs / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def is_inside_root(self, candidate: Path) -> bool:
        try:
            candidate.resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False
