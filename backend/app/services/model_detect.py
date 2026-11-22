from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .ai_review import validate_ai_base_url


@dataclass(slots=True)
class ModelDetection:
    models: list[str] = field(default_factory=list)
    suggested: str | None = None
    source: str = "none"
    suggested_base_url: str | None = None


_PROVIDER_DEFAULTS = (
    ("api.openai.com", "gpt-4o-mini"),
    ("openai.azure.com", "gpt-4o-mini"),
    ("api.deepseek.com", "deepseek-chat"),
    ("api.moonshot.cn", "moonshot-v1-8k"),
    ("dashscope.aliyuncs.com", "qwen-plus"),
    ("open.bigmodel.cn", "glm-4-flash"),
    ("api.siliconflow.cn", "Qwen/Qwen2.5-7B-Instruct"),
    ("openrouter.ai", "openai/gpt-4o-mini"),
    ("api.groq.com", "llama-3.3-70b-versatile"),
    ("api.mistral.ai", "mistral-small-latest"),
)
_PREFERRED = (
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "deepseek-chat",
    "qwen-plus",
    "glm-4-flash",
    "moonshot-v1-8k",
    "llama-3.3-70b-versatile",
    "mistral-small-latest",
)
_EXCLUDED_MARKERS = (
    "embed",
    "moderation",
    "whisper",
    "tts",
    "audio",
    "image",
    "dall-e",
    "rerank",
)


def models_endpoint(base_url: str) -> str:
    chat_endpoint = validate_ai_base_url(base_url)
    return chat_endpoint.removesuffix("/chat/completions") + "/models"


def _key_identity(api_key: str) -> tuple[str, str | None] | None:
    """按密钥格式识别服务商，返回（建议模型，建议 Base URL）。"""
    if api_key.startswith("sk-or-"):
        return "openai/gpt-4o-mini", "https://openrouter.ai/api/v1"
    if api_key.startswith("gsk_"):
        return "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1"
    if re.fullmatch(r"sk-[0-9a-fA-F]{32}", api_key):
        return "deepseek-chat", "https://api.deepseek.com/v1"
    if re.fullmatch(r"[0-9a-fA-F]{32}\.[A-Za-z0-9]{16}", api_key):
        return "glm-4-flash", "https://open.bigmodel.cn/api/paas/v4"
    if api_key.startswith("sk-proj-"):
        return "gpt-4o-mini", "https://api.openai.com/v1"
    if api_key.startswith("sk-"):
        return "gpt-4o-mini", None
    return None


def _provider_identity(base_url: str, api_key: str) -> tuple[str | None, str | None]:
    """密钥的强格式信号优先于地址；地址优先于通用 sk- 前缀。"""
    host = (urlsplit(base_url).hostname or "").lower()
    identity = _key_identity(api_key)
    if identity is not None and identity[1] is not None:
        model, provider_base = identity
        provider_host = (urlsplit(provider_base).hostname or "").lower()
        if provider_host != host:
            return model, provider_base
    for fragment, model in _PROVIDER_DEFAULTS:
        if fragment in host:
            return model, None
    if identity is not None:
        return identity[0], None
    return None, None


def _pick(models: list[str]) -> str | None:
    by_lower = {model.lower(): model for model in models}
    for preferred in _PREFERRED:
        if preferred.lower() in by_lower:
            return by_lower[preferred.lower()]
    return models[0] if models else None


async def detect_models(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: float = 8.0,
    client: httpx.AsyncClient | None = None,
) -> ModelDetection:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    identity_model, suggested_base = _provider_identity(base_url, api_key)
    try:
        response = await http.get(
            models_endpoint(base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        if response.status_code < 400:
            payload = response.json()
            raw = payload.get("data") if isinstance(payload, dict) else None
            if raw is None and isinstance(payload, dict):
                raw = payload.get("models")
            models: list[str] = []
            if isinstance(raw, list):
                for item in raw:
                    identifier = item.get("id") if isinstance(item, dict) else item
                    if not isinstance(identifier, str) or not identifier.strip():
                        continue
                    lowered = identifier.lower()
                    if any(marker in lowered for marker in _EXCLUDED_MARKERS):
                        continue
                    models.append(identifier.strip())
            models = list(dict.fromkeys(models))
            if models:
                suggested = identity_model if identity_model in models else _pick(models)
                return ModelDetection(models=models[:200], suggested=suggested, source="provider")
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    finally:
        if owns_client:
            await http.aclose()
    if identity_model:
        return ModelDetection(
            models=[identity_model],
            suggested=identity_model,
            source="heuristic",
            suggested_base_url=suggested_base,
        )
    return ModelDetection(source="none")
