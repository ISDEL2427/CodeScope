from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services.ai_review import validate_ai_base_url
from ..services.model_detect import detect_models


router = APIRouter(prefix="/api/ai", tags=["ai"])


class AISettingsPayload(BaseModel):
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=1000)
    remember: bool = True


class ModelDetectPayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=2000)
    api_key: str = Field(default="", max_length=1000)


def _settings_view(settings) -> dict[str, object]:
    return {
        "base_url": settings.base_url,
        "model": settings.model,
        "has_key": settings.has_key,
    }


@router.get("/settings")
async def read_ai_settings(request: Request) -> dict[str, object]:
    return _settings_view(request.app.state.ai_settings.load())


@router.post("/settings")
async def save_ai_settings(request: Request, payload: AISettingsPayload) -> dict[str, object]:
    store = request.app.state.ai_settings
    base_url = payload.base_url.strip()
    if base_url:
        try:
            validate_ai_base_url(base_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not payload.remember:
        return _settings_view(store.load())
    saved = store.save(
        base_url=base_url or None,
        model=payload.model.strip() or None,
        api_key=payload.api_key.strip() or None,
    )
    return _settings_view(saved)


@router.post("/models/detect")
async def detect_available_models(request: Request, payload: ModelDetectPayload) -> dict[str, object]:
    try:
        endpoint = validate_ai_base_url(payload.base_url.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    api_key = payload.api_key.strip()
    if not api_key:
        api_key = request.app.state.ai_settings.load().api_key
    if not api_key:
        raise HTTPException(status_code=422, detail="请先填写或保存 API Key。")
    result = await detect_models(endpoint, api_key)
    return {
        "models": result.models,
        "suggested": result.suggested,
        "source": result.source,
        "suggested_base_url": result.suggested_base_url,
    }
