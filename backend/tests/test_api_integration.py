from __future__ import annotations

import asyncio
import os
import random
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import ReviewStore
from backend.app.main import create_app
from backend.app.services.ai_review import AIReviewResult
from backend.app.services.model_detect import ModelDetection
from backend.app.services.osv_scan import DependencyScanResult

from conftest import (
    TEST_API_KEY,
    FakeOSVScanner,
    FakeReviewer,
    make_zip,
    review_form,
    wait_for_status,
)


def post_zip(client: TestClient, content: bytes, **form_overrides):
    form = review_form()
    form.update(form_overrides)
    return client.post(
        "/api/reviews",
        data=form,
        files={"file": ("sample.zip", content, "application/zip")},
    )


def test_health_and_zip_success_progress_cleanup_reports_and_key_secrecy(
    client: TestClient,
    settings: Settings,
    sample_zip: bytes,
    caplog,
    tmp_path: Path,
):
    assert client.get("/api/health").json() == {"status": "ok"}
    marker = tmp_path / "reviewed-code-executed"
    content = make_zip({"app.py": f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"})
    response = post_zip(client, content)
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["status"] == "queued"
    assert created["source_type"] == "zip"
    assert created["coverage"]["languages"] == {}

    result = wait_for_status(client, created["id"])
    assert result["status"] == "completed", result
    assert result["progress"] == 100
    assert result["stage"] == "completed"
    assert result["coverage"]["analyzed_files"] == 1
    assert result["coverage"]["languages"] == {"Python": 1}
    assert result["findings"][0]["source"] == "ai"
    assert result["vulnerabilities"][0]["vulnerability_id"] == "OSV-TEST-1"
    assert result["dependency_status"] == "completed"
    assert not marker.exists(), "reviewed source must never be executed"
    assert result["awaiting_decision"] is True
    assert result["depth"] == 1
    assert result["ai_budget"] == settings.max_ai_requests
    source_dir = settings.sources_dir / created["id"]
    assert (source_dir / "repo").is_dir(), "源码应保留到用户决定结束审查"
    assert not any(settings.uploads_dir.iterdir())

    listed = client.get("/api/reviews")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created["id"]
    assert "findings" not in listed.json()[0]
    assert "vulnerabilities" not in listed.json()[0]

    json_report = client.get(f"/api/reviews/{created['id']}/report?format=json")
    markdown = client.get(f"/api/reviews/{created['id']}/report?format=markdown")
    assert json_report.status_code == markdown.status_code == 200
    assert json_report.json()["id"] == created["id"]
    assert "需人工确认" in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]
    serialized_storage = (settings.runtime_dir / "reviews.db").read_bytes()
    assert TEST_API_KEY.encode() not in serialized_storage
    assert TEST_API_KEY not in json_report.text + markdown.text
    assert TEST_API_KEY not in caplog.text

    finished = client.post(f"/api/reviews/{created['id']}/finish")
    assert finished.status_code == 200, finished.text
    finished_body = finished.json()
    assert finished_body["awaiting_decision"] is False
    assert finished_body["ai_budget"] == settings.max_ai_requests
    assert finished_body["depth"] == 1
    assert not source_dir.exists()
    assert client.post(f"/api/reviews/{created['id']}/finish").status_code == 200
    assert client.post(f"/api/reviews/{created['id']}/continue").status_code == 409


def test_github_success_uses_mocked_clone(
    settings: Settings,
    monkeypatch,
):
    async def fake_clone(repo_url, destination, **kwargs):
        destination.mkdir(parents=True)
        (destination / "main.py").write_text("import sys\n", encoding="utf-8")
        from backend.app.services.ingest import validate_github_url
        return validate_github_url(repo_url)

    monkeypatch.setattr("backend.app.worker.clone_github", fake_clone)
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FakeReviewer(),
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as client:
        form = review_form()
        form.update(
            source_type="github",
            repo_url="https://github.com/owner/repo",
        )
        response = client.post("/api/reviews", data=form)
        assert response.status_code == 202, response.text
        assert response.json()["source_name"] == "owner/repo"
        result = wait_for_status(client, response.json()["id"])
        assert result["status"] == "completed"
        assert result["coverage"]["languages"] == {"Python": 1}


def test_invalid_source_and_corrupt_zip_failure_paths(client: TestClient):
    invalid = review_form()
    invalid.update(source_type="github", repo_url="https://evil.example/owner/repo")
    response = client.post("/api/reviews", data=invalid)
    assert response.status_code == 422
    assert "detail" in response.json()

    response = post_zip(client, b"not-a-zip")
    assert response.status_code == 202
    result = wait_for_status(client, response.json()["id"])
    assert result["status"] == "failed"
    assert result["error"]
    assert "detail" not in result


class FailingReviewer:
    async def review(self, *args, **kwargs):
        raise RuntimeError("external response contained secret credentials")


class FailingOSV:
    async def scan(self, root):
        raise RuntimeError("network unavailable")


def test_ai_and_osv_failures_still_complete_static_review(settings: Settings, sample_zip: bytes):
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FailingReviewer(),
        osv_scanner_factory=lambda: FailingOSV(),
    )
    with TestClient(app) as client:
        response = post_zip(client, sample_zip)
        result = wait_for_status(client, response.json()["id"])
        assert result["status"] == "completed"
        assert result["dependency_status"] == "unavailable"
        assert len(result["warnings"]) >= 2
        assert "secret credentials" not in " ".join(result["warnings"])


class BlockingReviewer:
    def __init__(self):
        self.started = threading.Event()

    async def review(self, inventory, chunks, *, cancel_event=None, progress_callback=None):
        self.started.set()
        await asyncio.Event().wait()


def test_running_review_can_be_cancelled_and_source_is_cleaned(
    settings: Settings,
    sample_zip: bytes,
):
    reviewer = BlockingReviewer()
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: reviewer,
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as client:
        response = post_zip(client, sample_zip)
        review_id = response.json()["id"]
        assert reviewer.started.wait(3), "review did not reach AI stage"
        running = client.get(f"/api/reviews/{review_id}").json()
        assert running["status"] == "running"
        cancelled = client.post(f"/api/reviews/{review_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        result = wait_for_status(client, review_id)
        assert result["status"] == "cancelled"
        assert result["completed_at"] is not None
        deadline = time.monotonic() + 2
        while any(settings.sources_dir.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(settings.sources_dir.iterdir())
        assert client.post(f"/api/reviews/{review_id}/cancel").status_code == 409


def test_lifespan_marks_legacy_tasks_interrupted_and_cleans_sources(settings: Settings):
    settings.runtime_dir.mkdir(parents=True)
    store = ReviewStore(settings.resolved_database_url)
    review = store.create("zip", "legacy.zip")
    store.update(review.id, status="running", stage="ai_review", progress=45)
    store.close()
    legacy_source = settings.sources_dir / review.id / "repo"
    legacy_source.mkdir(parents=True)
    (legacy_source / "secret.py").write_text("secret", encoding="utf-8")
    settings.uploads_dir.mkdir(parents=True)
    (settings.uploads_dir / "legacy.zip").write_bytes(b"zip")

    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FakeReviewer(),
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as client:
        recovered = client.get(f"/api/reviews/{review.id}").json()
        assert recovered["status"] == "interrupted"
        assert recovered["error"]
        assert not (settings.sources_dir / review.id).exists()
        assert not (settings.uploads_dir / "legacy.zip").exists()


def test_lifespan_keeps_sources_awaiting_decision(settings: Settings):
    settings.runtime_dir.mkdir(parents=True)
    store = ReviewStore(settings.resolved_database_url)
    review = store.create("zip", "kept.zip", ai_budget=settings.max_ai_requests)
    store.update(
        review.id,
        status="completed",
        stage="completed",
        progress=100,
        awaiting_decision=True,
        covered_files=["app.py"],
    )
    store.close()
    kept_repo = settings.sources_dir / review.id / "repo"
    kept_repo.mkdir(parents=True)
    (kept_repo / "app.py").write_text("print('kept')\n", encoding="utf-8")
    stale = settings.sources_dir / "stale-review" / "repo"
    stale.mkdir(parents=True)

    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FakeReviewer(),
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as client:
        assert kept_repo.is_dir()
        assert not (settings.sources_dir / "stale-review").exists()
        current = client.get(f"/api/reviews/{review.id}").json()
        assert current["awaiting_decision"] is True

        assert client.post(f"/api/reviews/{review.id}/finish").status_code == 200
        assert not (settings.sources_dir / review.id).exists()


def test_api_404_and_report_format_errors(client: TestClient):
    assert client.get("/api/reviews/not-found").status_code == 404
    assert client.get("/api/reviews/not-found/report").status_code == 404
    response = post_zip(client, make_zip())
    review_id = response.json()["id"]
    wait_for_status(client, review_id)
    assert client.get(f"/api/reviews/{review_id}/report?format=xml").status_code == 422


class RecordingReviewer(FakeReviewer):
    def __init__(self):
        self.calls: list[set[str]] = []

    async def review(self, inventory, chunks, *, cancel_event=None, progress_callback=None):
        self.calls.append(
            {path for chunk in chunks for path in (chunk.file_paths or (chunk.file_path,))}
        )
        return await super().review(
            inventory,
            chunks,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )


def _large_module(seed: int) -> str:
    random_source = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    lines = [
        "# " + "".join(random_source.choice(alphabet) for _ in range(80))
        for _ in range(150)
    ]
    return "\n".join(lines) + "\nprint(1)\n"


def test_continue_deep_review_doubles_budget_and_skips_covered(settings: Settings):
    reviewer = RecordingReviewer()
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: reviewer,
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    files = {f"module_{index}.py": _large_module(index) for index in range(8)}
    with TestClient(app) as client:
        response = post_zip(client, make_zip(files))
        assert response.status_code == 202, response.text
        review_id = response.json()["id"]
        first = wait_for_status(client, review_id)
        assert first["awaiting_decision"] is True
        assert first["depth"] == 1
        assert first["ai_budget"] == settings.max_ai_requests
        assert first["coverage"]["analyzed_files"] == 5
        assert len(reviewer.calls[0]) == 5
        covered_first_round = set(reviewer.calls[0])

        continued = client.post(
            f"/api/reviews/{review_id}/continue",
            data={"base_url": "https://ai.example.test/v1", "model": "mock-model"},
        )
        assert continued.status_code == 200, continued.text
        assert continued.json()["depth"] == 2
        assert continued.json()["ai_budget"] == settings.max_ai_requests * 2

        second = wait_for_status(client, review_id)
        assert second["status"] == "completed"
        assert second["awaiting_decision"] is True
        assert second["depth"] == 2
        assert second["ai_budget"] == settings.max_ai_requests * 2
        assert second["coverage"]["analyzed_files"] == 8
        assert set(reviewer.calls[1]) == set(files) - covered_first_round
        assert len(second["findings"]) == 8

        finished = client.post(f"/api/reviews/{review_id}/finish")
        assert finished.status_code == 200
        assert finished.json()["ai_budget"] == settings.max_ai_requests
        assert finished.json()["depth"] == 1
        assert not (settings.sources_dir / review_id).exists()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅在 Windows 可用")
def test_api_key_is_remembered_and_reused(settings: Settings, sample_zip: bytes):
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FakeReviewer(),
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as client:
        response = post_zip(client, sample_zip)
        assert wait_for_status(client, response.json()["id"])["status"] == "completed"

        saved = client.get("/api/ai/settings").json()
        assert saved["has_key"] is True
        assert saved["base_url"] == "https://ai.example.test/v1"
        assert saved["model"] == "mock-model"
        raw_settings = (settings.runtime_dir / "ai_settings.json").read_text(encoding="utf-8")
        assert TEST_API_KEY not in raw_settings
        assert "dpapi:" in raw_settings

        form = review_form()
        form.pop("api_key")
        without_key = client.post(
            "/api/reviews",
            data=form,
            files={"file": ("sample.zip", sample_zip, "application/zip")},
        )
        assert without_key.status_code == 202, without_key.text
        assert wait_for_status(client, without_key.json()["id"])["status"] == "completed"


def test_model_detection_endpoint_returns_suggestion(settings: Settings, monkeypatch):
    async def fake_detect(base_url: str, api_key: str, **kwargs) -> ModelDetection:
        assert base_url == "https://api.openai.com/v1/chat/completions"
        assert api_key == TEST_API_KEY
        return ModelDetection(models=["gpt-4o-mini"], suggested="gpt-4o-mini", source="provider")

    monkeypatch.setattr("backend.app.api.ai.detect_models", fake_detect)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/ai/models/detect",
            json={"base_url": "https://api.openai.com/v1", "api_key": TEST_API_KEY},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "models": ["gpt-4o-mini"],
            "suggested": "gpt-4o-mini",
            "source": "provider",
            "suggested_base_url": None,
        }


def test_built_frontend_assets_and_spa_routes_are_served(
    settings: Settings,
    tmp_path: Path,
    monkeypatch,
):
    dist = tmp_path / "frontend-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    html = "<!doctype html><title>CodeScope</title><div id=\"root\"></div>"
    (dist / "index.html").write_text(html, encoding="utf-8")
    (assets / "app.js").write_text("window.codeScope = true;", encoding="utf-8")
    monkeypatch.setenv("CODE_REVIEW_FRONTEND_DIST", str(dist))
    app = create_app(
        settings,
        ai_reviewer_factory=lambda job: FakeReviewer(),
        osv_scanner_factory=lambda: FakeOSVScanner(),
    )
    with TestClient(app) as client:
        assert client.get("/").text == html
        assert client.get("/reviews/example").text == html
        assert client.get("/assets/app.js").text == "window.codeScope = true;"
        missing_api = client.get("/api/not-found")
        assert missing_api.status_code == 404
        assert missing_api.json()["detail"] == "接口不存在。"
