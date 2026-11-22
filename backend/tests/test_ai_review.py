from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.app.services.ai_review import OpenAICompatibleReviewer, validate_ai_base_url
from backend.app.services.chunker import CodeChunk
from backend.app.services.inventory import build_inventory


def finding(**overrides):
    value = {
        "category": "bug",
        "severity": "high",
        "title": "有效问题",
        "description": "说明",
        "suggestion": "建议",
        "file_path": "app.py",
        "line_start": 2,
        "line_end": 3,
        "evidence": "def handler():\n    return 42",
    }
    value.update(overrides)
    return value


def openai_response(payload) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
    )


@pytest.fixture
def inventory(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "import os\ndef handler():\n    return 42\n", encoding="utf-8"
    )
    return build_inventory(tmp_path)


def make_chunk(inventory) -> CodeChunk:
    source = inventory.files[0]
    return CodeChunk("app.py", "Python", 1, 3, source.read_text())


@pytest.mark.asyncio
async def test_ai_discards_invalid_paths_lines_and_evidence(inventory):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return openai_response(
                {
                    "summary": "模块摘要",
                    "findings": [
                        finding(),
                        finding(title="严重问题", severity="critical"),
                        finding(title="坏路径", file_path="../app.py"),
                        finding(title="不存在路径", file_path="other.py"),
                        finding(title="坏行号", line_start=99, line_end=100),
                        finding(title="坏证据", evidence="not in source"),
                        finding(title="倒序", line_start=3, line_end=2),
                    ],
                }
            )
        return openai_response({"summary": "综合", "findings": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAICompatibleReviewer(
            base_url="https://ai.example/v1",
            api_key="test-secret-key",
            model="test-model",
            client=client,
            max_retries=0,
        )
        result = await reviewer.review(inventory, [make_chunk(inventory)])

    assert len(result.findings) == 2
    by_title = {item.title: item for item in result.findings}
    assert not by_title["有效问题"].requires_human_review
    assert by_title["严重问题"].requires_human_review
    assert result.summaries == ["app.py: 模块摘要"]
    assert len(calls) == 2
    assert all(request.headers["Authorization"] == "Bearer test-secret-key" for request in calls)
    assert not any("坏路径" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_ai_allows_one_json_repair(inventory):
    responses = iter(
        [
            httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]}),
            openai_response({"summary": "修复", "findings": [finding()]}),
            openai_response({"summary": "综合", "findings": []}),
        ]
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses))) as client:
        reviewer = OpenAICompatibleReviewer(
            base_url="https://ai.example/v1/chat/completions",
            api_key="key",
            model="model",
            client=client,
            max_retries=0,
        )
        result = await reviewer.review(inventory, [make_chunk(inventory)])

    assert len(result.findings) == 1
    assert result.summaries == ["app.py: 修复"]
    assert result.requests == 3


@pytest.mark.asyncio
async def test_ai_bad_json_after_repair_degrades_without_throwing(inventory):
    responses = iter(
        [
            httpx.Response(200, json={"choices": [{"message": {"content": "bad"}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "still-bad"}}]}),
            openai_response({"summary": "综合", "findings": []}),
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses))) as client:
        reviewer = OpenAICompatibleReviewer(
            base_url="https://ai.example/v1",
            api_key="key",
            model="model",
            client=client,
            max_retries=0,
        )
        result = await reviewer.review(inventory, [make_chunk(inventory)])
    assert result.findings == []
    assert any("非法 JSON" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_ai_http_error_is_generic_and_does_not_leak_response_secret(inventory):
    def handler(request: httpx.Request):
        return httpx.Response(401, text="invalid key test-secret-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAICompatibleReviewer(
            base_url="https://ai.example/v1",
            api_key="test-secret-key",
            model="model",
            client=client,
            max_retries=0,
        )
        result = await reviewer.review(inventory, [make_chunk(inventory)])
    assert result.findings == []
    assert result.warnings
    assert "test-secret-key" not in " ".join(result.warnings)


@pytest.mark.asyncio
async def test_ai_401_reports_provider_detail_and_collapses_repeated_failures(inventory):
    def handler(request: httpx.Request):
        return httpx.Response(
            401,
            json={"error": {"message": "Incorrect API key provided: sk-live-abcdef123456"}},
        )

    chunks = [make_chunk(inventory), make_chunk(inventory)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAICompatibleReviewer(
            base_url="https://ai.example/v1",
            api_key="sk-live-abcdef123456",
            model="model",
            client=client,
            max_retries=0,
        )
        result = await reviewer.review(inventory, chunks)

    assert result.findings == []
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "HTTP 401" in warning and "API Key" in warning
    assert "sk-live-abcdef123456" not in warning


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com", "https://user:pass@example.com/v1", "https://example.com/v1?key=x"],
)
def test_ai_base_url_rejects_credentials_and_unsupported_schemes(url: str):
    with pytest.raises(ValueError):
        validate_ai_base_url(url)


def test_ai_normalizes_severity_and_marks_only_critical_for_review():
    reviewer = OpenAICompatibleReviewer(
        base_url="https://ai.example/v1",
        api_key="key",
        model="model",
    )
    raw = [
        dict(finding(title="大写等级", severity=" HIGH ", line_start=1, line_end=1, evidence="a = 1")),
        dict(finding(title="未知等级", severity="moderate", line_start=2, line_end=2, evidence="b = 2")),
        dict(finding(title="严重等级", severity="Critical", line_start=1, line_end=1, evidence="a = 1")),
    ]
    warnings: list[str] = []

    findings = reviewer._validate_findings(raw, {"app.py": "a = 1\nb = 2\n"}, warnings)

    by_title = {item.title: item for item in findings}
    assert by_title["大写等级"].severity == "high"
    assert by_title["未知等级"].severity == "medium"
    assert by_title["严重等级"].severity == "critical"
    assert by_title["严重等级"].requires_human_review
    assert not by_title["大写等级"].requires_human_review
    assert not warnings
