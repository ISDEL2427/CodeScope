from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from ..models import BaselineResponse, Review, ReviewSummary
from ..services.ai_review import validate_ai_base_url
from ..services.baseline import compare_reviews
from ..services.ingest import SourceIngestError, remove_tree, save_upload, validate_github_url
from ..services.report import render_json, render_markdown, safe_report_filename
from ..worker import ReviewJob


router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.post("", response_model=Review, status_code=status.HTTP_202_ACCEPTED)
async def create_review(
    request: Request,
    source_type: Literal["github", "zip"] = Form(...),
    base_url: str = Form(..., min_length=1, max_length=2000),
    api_key: str | None = Form(default=None, max_length=1000),
    model: str = Form(..., min_length=1, max_length=300),
    remember_key: bool = Form(default=True),
    repo_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> Review:
    try:
        endpoint = validate_ai_base_url(base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not model.strip():
        raise HTTPException(status_code=422, detail="AI 模型名不能为空。")
    ai_settings = request.app.state.ai_settings
    resolved_key = (api_key or "").strip()
    if not resolved_key:
        resolved_key = ai_settings.load().api_key
    if not resolved_key:
        raise HTTPException(status_code=422, detail="请填写 API Key，或先在本机保存密钥。")

    review_id = str(uuid.uuid4())
    upload_path = None
    if source_type == "github":
        if file is not None:
            await file.close()
            raise HTTPException(status_code=422, detail="GitHub 与 ZIP 来源不能同时提供。")
        if not repo_url:
            raise HTTPException(status_code=422, detail="GitHub 审查必须提供 repo_url。")
        try:
            source = validate_github_url(repo_url)
        except SourceIngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source_name = source.name
        canonical_repo_url = source.url
    else:
        if repo_url:
            raise HTTPException(status_code=422, detail="ZIP 审查不能同时提供 repo_url。")
        if file is None:
            raise HTTPException(status_code=422, detail="ZIP 审查必须上传 file。")
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.lower().endswith(".zip"):
            await file.close()
            raise HTTPException(status_code=422, detail="上传文件必须使用 .zip 扩展名。")
        source_name = filename
        canonical_repo_url = None
        upload_path = request.app.state.settings.uploads_dir / f"{review_id}.zip"
        try:
            await save_upload(file, upload_path)
        except SourceIngestError as exc:
            code = 413 if "50 MiB" in str(exc) else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    store = request.app.state.store
    settings = request.app.state.settings
    try:
        review = store.create(
            source_type,
            source_name,
            review_id=review_id,
            ai_budget=settings.max_ai_requests,
            ai_base_url=base_url.strip(),
            ai_model=model.strip(),
        )
        job = ReviewJob(
            review_id=review_id,
            source_type=source_type,
            source_name=source_name,
            base_url=endpoint,
            api_key=resolved_key,
            model=model.strip(),
            repo_url=canonical_repo_url,
            upload_path=upload_path,
            max_requests=settings.max_ai_requests,
        )
        await request.app.state.worker.enqueue(job)
    except Exception:
        if upload_path:
            upload_path.unlink(missing_ok=True)
        raise
    if remember_key and (api_key or "").strip():
        try:
            ai_settings.save(
                base_url=base_url.strip(),
                model=model.strip(),
                api_key=resolved_key,
            )
        except OSError:
            pass
    return review


@router.get("", response_model=list[ReviewSummary])
async def list_reviews(request: Request) -> list[ReviewSummary]:
    return request.app.state.store.list()


@router.get("/{review_id}", response_model=Review)
async def get_review(request: Request, review_id: str) -> Review:
    review = request.app.state.store.get(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    return review


@router.post("/{review_id}/cancel", response_model=Review)
async def cancel_review(request: Request, review_id: str) -> Review:
    store = request.app.state.store
    review = store.get(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    if review.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="任务已结束，无法取消。")
    await request.app.state.worker.cancel(review_id)
    cancelled, changed = store.cancel_active(review_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    if not changed:
        raise HTTPException(status_code=409, detail="任务已结束，无法取消。")
    return cancelled


@router.post("/{review_id}/continue", response_model=Review)
async def continue_review(
    request: Request,
    review_id: str,
    base_url: str = Form(default="", max_length=2000),
    api_key: str | None = Form(default=None, max_length=1000),
    model: str = Form(default="", max_length=300),
) -> Review:
    store = request.app.state.store
    record = store.get_record(review_id)
    if record is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    if record.status != "completed" or not record.awaiting_decision:
        raise HTTPException(status_code=409, detail="当前任务不可继续深入审查。")

    settings = request.app.state.settings
    ai_settings = request.app.state.ai_settings
    resolved_base = (base_url or record.ai_base_url or "").strip()
    resolved_model = (model or record.ai_model or "").strip()
    try:
        endpoint = validate_ai_base_url(resolved_base)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not resolved_model:
        raise HTTPException(status_code=422, detail="AI 模型名不能为空。")
    resolved_key = (api_key or "").strip() or ai_settings.load().api_key
    if not resolved_key:
        raise HTTPException(status_code=422, detail="请重新输入 API Key，或先在新建审查页保存密钥。")

    depth = (record.depth or 1) + 1
    budget = max(record.ai_budget or settings.max_ai_requests, 1) * 2
    updated = store.update(
        review_id,
        status="running",
        stage="inventory",
        stage_detail="正在准备深入审查",
        progress=2,
        error=None,
        completed_at=None,
        awaiting_decision=False,
        depth=depth,
        ai_budget=budget,
        ai_base_url=resolved_base,
        ai_model=resolved_model,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    job = ReviewJob(
        review_id=review_id,
        source_type=record.source_type,
        source_name=record.source_name,
        base_url=endpoint,
        api_key=resolved_key,
        model=resolved_model,
        mode="continue",
        max_requests=budget,
    )
    await request.app.state.worker.enqueue(job)
    if (api_key or "").strip():
        try:
            ai_settings.save(
                base_url=resolved_base,
                model=resolved_model,
                api_key=resolved_key,
            )
        except OSError:
            pass
    return updated


@router.post("/{review_id}/finish", response_model=Review)
async def finish_review(request: Request, review_id: str) -> Review:
    store = request.app.state.store
    record = store.get_record(review_id)
    if record is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    if record.status != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成，暂时无法结束审查。")
    settings = request.app.state.settings
    await asyncio.to_thread(remove_tree, settings.sources_dir / review_id)
    updated = store.update(
        review_id,
        awaiting_decision=False,
        ai_budget=settings.max_ai_requests,
        depth=1,
        stage_detail="审查已结束；临时源码已清理",
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    return updated


@router.get("/{review_id}/baseline", response_model=BaselineResponse)
async def review_baseline(request: Request, review_id: str) -> BaselineResponse:
    review = request.app.state.store.get(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    if review.status != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成，暂无可对比的体检结果。")
    baseline = request.app.state.store.previous_completed(
        review.id, review.source_type, review.source_name, review.created_at
    )
    if baseline is None:
        return BaselineResponse(
            message="未找到同一目标更早完成的审查；这是该目标的首次体检。GitHub 仓库按 owner/repo 匹配，ZIP 按文件名匹配。"
        )
    return BaselineResponse(baseline=compare_reviews(review, baseline))


@router.get("/{review_id}/report")
async def download_report(
    request: Request,
    review_id: str,
    format: Literal["json", "markdown"] = "json",
) -> Response:
    review = request.app.state.store.get(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="审查任务不存在。")
    content = render_json(review) if format == "json" else render_markdown(review)
    media_type = "application/json" if format == "json" else "text/markdown"
    filename = safe_report_filename(review, format)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
