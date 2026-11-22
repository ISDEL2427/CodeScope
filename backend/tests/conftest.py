from __future__ import annotations

import asyncio
import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Finding, Vulnerability
from backend.app.services.ai_review import AIReviewResult
from backend.app.services import osv_scan
from backend.app.services.osv_scan import DependencyScanResult


TEST_API_KEY = "sk-test-DO-NOT-PERSIST-1234567890"


def make_zip(files: dict[str, str | bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in (files or {"app.py": "import os\nprint('safe')\n"}).items():
            bundle.writestr(name, content)
    return output.getvalue()


class FakeReviewer:
    def review(
        self,
        inventory,
        chunks,
        *,
        cancel_event=None,
        progress_callback=None,
    ):
        return self._review(inventory, chunks, cancel_event, progress_callback)

    async def _review(self, inventory, chunks, cancel_event, progress_callback) -> AIReviewResult:
        findings = []
        summaries = []
        covered_files: set[str] = set()
        complete_files: set[str] = set()
        for index, chunk in enumerate(chunks, start=1):
            complete_files.update(chunk.complete_paths)
            for path in chunk.file_paths or (chunk.file_path,):
                source = next(item for item in inventory.files if item.path == path)
                first_line = source.read_text().splitlines()[0]
                findings.append(
                    Finding(
                        id=f"ai-fake-{index}-{len(findings)}",
                        source="ai",
                        category="maintainability",
                        severity="info",
                        title="示例 AI 发现",
                        description="mock AI 发现，需人工确认",
                        suggestion="人工复核该代码。",
                        file_path=source.path,
                        line_start=1,
                        line_end=1,
                        evidence=first_line,
                        requires_human_review=True,
                    )
                )
                summaries.append(f"{source.path}: mock 模块摘要")
                covered_files.add(path)
            if progress_callback:
                await progress_callback(index, len(chunks))
        return AIReviewResult(
            findings=findings,
            summaries=summaries,
            requests=len(chunks) + 1,
            covered_files=covered_files,
            complete_files=complete_files,
        )


class FakeOSVScanner:
    async def scan(self, root: Path) -> DependencyScanResult:
        return DependencyScanResult(
            status="completed",
            vulnerabilities=[
                Vulnerability(
                    package_name="demo",
                    package_version="1.0.0",
                    ecosystem="PyPI",
                    vulnerability_id="OSV-TEST-1",
                    summary="mock 漏洞",
                    severity="high",
                    references=["https://osv.dev/OSV-TEST-1"],
                )
            ],
            dependency_count=1,
        )


def wait_for_status(client: TestClient, review_id: str, terminal=True, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/reviews/{review_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if terminal and last["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return last
        if not terminal and last["status"] == "running":
            return last
        time.sleep(0.01)
    raise AssertionError(f"review did not reach expected state: {last}")


@pytest.fixture(autouse=True)
def clear_osv_cache():
    osv_scan._cache.clear()
    yield
    osv_scan._cache.clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_dir=tmp_path / "runtime",
        ai_max_retries=0,
        osv_max_retries=0,
        max_ai_requests=6,
    )


@pytest.fixture
def sample_zip() -> bytes:
    return make_zip()


@pytest.fixture
def client(settings: Settings):
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FakeReviewer(),
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as test_client:
        yield test_client


def review_form() -> dict[str, str]:
    return {
        "source_type": "zip",
        "base_url": "https://ai.example.test/v1",
        "api_key": TEST_API_KEY,
        "model": "mock-model",
    }
