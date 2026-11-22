from __future__ import annotations

import asyncio

import httpx

from backend.app.services.model_detect import detect_models


def _run(base_url: str, api_key: str, handler) -> object:
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await detect_models(base_url, api_key, client=client)

    return asyncio.run(run())


def test_detect_models_filters_and_prefers_provider_model():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "text-embedding-3-small"},
                    {"id": "deepseek-chat"},
                    {"id": "gpt-4o-mini"},
                ]
            },
        )

    result = _run("https://api.deepseek.com/v1", "sk-test", handler)
    assert result.models == ["deepseek-chat", "gpt-4o-mini"]
    assert result.suggested == "deepseek-chat"
    assert result.source == "provider"


def test_detect_models_falls_back_to_host_heuristic():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    result = _run("https://api.deepseek.com/v1", "sk-test", handler)
    assert result.models == ["deepseek-chat"]
    assert result.suggested == "deepseek-chat"
    assert result.source == "heuristic"


def test_detect_models_falls_back_to_key_prefix():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    result = _run("https://example.test/v1", "gsk_example", handler)
    assert result.suggested == "llama-3.3-70b-versatile"
    assert result.source == "heuristic"


def test_detect_models_recognizes_deepseek_key_format():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    result = _run(
        "https://api.openai.com/v1",
        "sk-00000000000000000000000000000000",
        handler,
    )
    assert result.suggested == "deepseek-chat"
    assert result.suggested_base_url == "https://api.deepseek.com/v1"
    assert result.source == "heuristic"


def test_detect_models_keeps_host_model_for_generic_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    result = _run("https://api.deepseek.com/v1", "sk-generic-key", handler)
    assert result.suggested == "deepseek-chat"
    assert result.suggested_base_url is None
