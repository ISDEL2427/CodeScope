from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.app.config import Settings
from backend.app.database import ReviewStore
from backend.app.models import Finding, Vulnerability
from backend.app.services.osv_scan import OSVScanner, discover_dependencies
from backend.app.services.report import render_json, render_markdown, safe_report_filename


def create_manifests(root: Path) -> None:
    (root / "requirements.txt").write_text(
        "requests==2.31.0\nflask>=2.0\n# ignored==1.0\n-r other.txt\n",
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "root"},
                    "node_modules/@scope/demo": {"version": "1.2.3"},
                    "node_modules/nested/node_modules/demo": {"version": "2.0.0"},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "poetry.lock").write_text(
        '[[package]]\nname = "django"\nversion = "4.2.0"\n', encoding="utf-8"
    )
    (root / "uv.lock").write_text(
        '[[package]]\nname = "fastapi"\nversion = "0.115.0"\n', encoding="utf-8"
    )


def test_dependency_parsers_support_only_locked_versions(tmp_path: Path):
    create_manifests(tmp_path)
    dependencies, warnings = discover_dependencies(tmp_path)
    values = {(item.ecosystem, item.name, item.version) for item in dependencies}
    assert ("PyPI", "requests", "2.31.0") in values
    assert ("npm", "@scope/demo", "1.2.3") in values
    assert ("npm", "demo", "2.0.0") in values
    assert ("PyPI", "django", "4.2.0") in values
    assert ("PyPI", "fastapi", "0.115.0") in values
    assert not any(item.name == "flask" for item in dependencies)
    assert warnings == []


@pytest.mark.asyncio
async def test_osv_querybatch_is_mocked_and_maps_independent_results(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/querybatch"
        payload = json.loads(request.content)
        assert payload["queries"][0]["package"] == {"name": "requests", "ecosystem": "PyPI"}
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "vulns": [
                            {
                                "id": "GHSA-test",
                                "summary": "测试漏洞",
                                "database_specific": {"severity": "HIGH"},
                                "references": [{"url": "https://osv.dev/GHSA-test"}],
                            }
                        ]
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        scanner = OSVScanner(client=client, max_retries=0)
        result = await scanner.scan(tmp_path)
    assert result.status == "completed"
    assert result.dependency_count == 1
    vulnerability = result.vulnerabilities[0]
    assert vulnerability.vulnerability_id == "GHSA-test"
    assert vulnerability.summary == "测试漏洞"
    assert vulnerability.severity == "high"
    assert vulnerability.references == ["https://osv.dev/GHSA-test"]


@pytest.mark.asyncio
async def test_osv_failure_marks_only_dependency_scan_unavailable(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")

    def handler(request: httpx.Request):
        raise httpx.ConnectError("network disabled", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OSVScanner(client=client, max_retries=0).scan(tmp_path)
    assert result.status == "unavailable"
    assert result.vulnerabilities == []
    assert "OSV" in result.warnings[-1]


def populated_review(tmp_path: Path):
    store = ReviewStore(f"sqlite:///{(tmp_path / 'report.db').as_posix()}")
    review = store.create("github", "owner/repo")
    finding = Finding(
        id="static-1",
        source="static",
        category="security",
        severity="high",
        title="测试发现",
        description="说明",
        suggestion="建议",
        file_path="app.py",
        line_start=1,
        line_end=1,
        evidence="password = '[REDACTED]'",
    )
    vulnerability = Vulnerability(
        package_name="demo",
        package_version="1.0.0",
        ecosystem="PyPI",
        vulnerability_id="OSV-1",
        summary="摘要",
        severity="high",
        references=["https://osv.dev/OSV-1"],
    )
    store.update(
        review.id,
        status="completed",
        findings=[finding],
        vulnerabilities=[vulnerability],
        warnings=["部分文件未覆盖"],
    )
    return store, store.get(review.id)


def test_reports_share_structure_have_disclaimer_and_safe_filename(tmp_path: Path):
    store, review = populated_review(tmp_path)
    secret = "sk-never-store-this-key"
    json_report = render_json(review)
    markdown = render_markdown(review)
    parsed = json.loads(json_report)
    assert parsed["findings"][0]["evidence"] == review.findings[0].evidence
    assert parsed["vulnerabilities"][0]["vulnerability_id"] == "OSV-1"
    assert "需人工确认" in markdown
    assert "部分文件未覆盖" in markdown
    assert secret not in json_report + markdown
    filename = safe_report_filename(review, "markdown")
    assert "/" not in filename and "\\" not in filename
    assert filename.endswith(".md")
    store.close()


def test_database_recovers_interrupted_tasks(tmp_path: Path):
    store = ReviewStore(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    review = store.create("zip", "sample.zip")
    store.update(review.id, status="running", stage="ai_review", progress=50)
    assert store.recover_interrupted() == 1
    recovered = store.get(review.id)
    assert recovered.status == "interrupted"
    assert recovered.completed_at is not None
    assert recovered.error
    store.close()


def test_runtime_and_database_environment_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CODE_REVIEW_RUNTIME_DIR", str(tmp_path / "custom-runtime"))
    monkeypatch.setenv("CODE_REVIEW_DATABASE_URL", "sqlite:///custom.db")
    settings = Settings.from_env()
    assert settings.runtime_dir == (tmp_path / "custom-runtime").resolve()
    assert settings.resolved_database_url == "sqlite:///custom.db"
