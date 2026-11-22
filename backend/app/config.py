from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    runtime_dir: Path
    database_url: str | None = None
    github_timeout_seconds: float = 300.0
    github_max_bytes: int = 512 * 1024 * 1024
    ai_timeout_seconds: float = 60.0
    ai_max_retries: int = 2
    osv_timeout_seconds: float = 20.0
    osv_max_retries: int = 2
    max_ai_requests: int = 150

    @classmethod
    def from_env(cls) -> "Settings":
        runtime = (
            os.getenv("CODE_REVIEW_RUNTIME_DIR")
            or os.getenv("REVIEW_RUNTIME_DIR")
            or os.getenv("RUNTIME_DIR")
        )
        database = (
            os.getenv("CODE_REVIEW_DATABASE_URL")
            or os.getenv("REVIEW_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        return cls(
            runtime_dir=Path(runtime).expanduser().resolve()
            if runtime
            else PROJECT_ROOT / "runtime",
            database_url=database or None,
        )

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.runtime_dir / 'reviews.db').as_posix()}"

    @property
    def sources_dir(self) -> Path:
        return self.runtime_dir / "sources"

    @property
    def uploads_dir(self) -> Path:
        return self.runtime_dir / "uploads"

    @property
    def frontend_dist(self) -> Path:
        override = os.getenv("CODE_REVIEW_FRONTEND_DIST")
        if override:
            return Path(override).expanduser().resolve()
        return PROJECT_ROOT / "frontend" / "dist"
