from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api.ai import router as ai_router
from .api.reviews import router as reviews_router
from .config import Settings
from .database import ReviewStore
from .services.ai_credentials import AISettingsStore
from .worker import AIReviewerFactory, OSVScannerFactory, ReviewWorker


def prune_runtime_artifacts(settings: Settings, retained_sources: set[str]) -> None:
    """清理临时源码，仅保留仍等待用户决定是否继续深入审查的任务。"""
    for directory in (settings.sources_dir, settings.uploads_dir):
        directory.mkdir(parents=True, exist_ok=True)
        for child in directory.iterdir():
            if directory == settings.sources_dir and child.name in retained_sources:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass


def create_app(
    settings: Settings | None = None,
    *,
    ai_reviewer_factory: AIReviewerFactory | None = None,
    osv_scanner_factory: OSVScannerFactory | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        store = ReviewStore(settings.resolved_database_url)
        ai_settings = AISettingsStore(settings.runtime_dir / "ai_settings.json")
        store.recover_interrupted()
        prune_runtime_artifacts(settings, store.retained_source_ids())
        worker = ReviewWorker(
            store,
            settings,
            ai_reviewer_factory=ai_reviewer_factory,
            osv_scanner_factory=osv_scanner_factory,
        )
        app.state.settings = settings
        app.state.store = store
        app.state.worker = worker
        app.state.ai_settings = ai_settings
        await worker.start()
        try:
            yield
        finally:
            await worker.stop()
            store.close()

    app = FastAPI(
        title="Local AI Code Review",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(reviews_router)
    app.include_router(ai_router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str) -> FileResponse:
        frontend_dist = settings.frontend_dist.resolve()
        index = frontend_dist / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="前端页面尚未构建。")
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="接口不存在。")
        if full_path:
            candidate = (frontend_dist / full_path).resolve()
            if frontend_dist not in candidate.parents:
                raise HTTPException(status_code=404, detail="页面不存在。")
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)

    return app


app = create_app()
