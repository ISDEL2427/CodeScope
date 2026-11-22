from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.models import Finding, Review, Vulnerability
from backend.app.services.baseline import compare_reviews

from conftest import make_zip, review_form, wait_for_status


START = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


def make_finding(
    finding_id: str,
    *,
    title: str = "检测到疑似硬编码密钥",
    file_path: str = "src/config.ts",
    line: int = 12,
    source: str = "static",
    category: str = "security",
    severity: str = "high",
) -> Finding:
    return Finding(
        id=finding_id,
        source=source,
        category=category,
        severity=severity,
        title=title,
        description="示例发现",
        suggestion="示例建议",
        file_path=file_path,
        line_start=line,
        line_end=line,
        evidence="const value = 'REDACTED';",
    )


def make_vulnerability(
    vulnerability_id: str,
    *,
    package: str = "lodash",
    version: str = "4.17.20",
) -> Vulnerability:
    return Vulnerability(
        package_name=package,
        package_version=version,
        ecosystem="npm",
        vulnerability_id=vulnerability_id,
        summary="示例漏洞",
    )


def make_review(
    review_id: str,
    *,
    offset_minutes: int = 0,
    findings: list[Finding] | None = None,
    vulnerabilities: list[Vulnerability] | None = None,
) -> Review:
    created_at = START + timedelta(minutes=offset_minutes)
    return Review(
        id=review_id,
        source_type="zip",
        source_name="demo.zip",
        status="completed",
        stage="completed",
        progress=100,
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at,
        findings=findings or [],
        vulnerabilities=vulnerabilities or [],
    )


def test_matches_across_line_shift_and_title_punctuation():
    baseline = make_review("base", findings=[make_finding("a", line=12)])
    current = make_review(
        "current",
        offset_minutes=5,
        findings=[make_finding("b", line=40, title="检测到疑似硬编码密钥。")],
    )

    comparison = compare_reviews(current, baseline)

    assert comparison.persisting_findings == current.findings
    assert comparison.new_findings == []
    assert comparison.resolved_findings == []


def test_splits_new_resolved_and_persisting_findings():
    baseline = make_review(
        "base",
        findings=[
            make_finding("keep", file_path="src/a.ts"),
            make_finding("gone", file_path="src/b.ts"),
        ],
    )
    current = make_review(
        "current",
        offset_minutes=5,
        findings=[
            make_finding("keep", file_path="src/a.ts"),
            make_finding("added", file_path="src/c.ts"),
        ],
    )

    comparison = compare_reviews(current, baseline)

    assert [item.id for item in comparison.persisting_findings] == ["keep"]
    assert [item.id for item in comparison.new_findings] == ["added"]
    assert [item.id for item in comparison.resolved_findings] == ["gone"]


def test_duplicate_findings_are_matched_one_to_one():
    baseline = make_review("base", findings=[make_finding("first", line=3)])
    current = make_review(
        "current",
        offset_minutes=5,
        findings=[make_finding("one", line=3), make_finding("two", line=9)],
    )

    comparison = compare_reviews(current, baseline)

    assert len(comparison.persisting_findings) == 1
    assert len(comparison.new_findings) == 1


def test_dependency_vulnerabilities_compare_by_id_not_version():
    baseline = make_review(
        "base",
        vulnerabilities=[make_vulnerability("GHSA-1", version="1.0.0"), make_vulnerability("GHSA-2")],
    )
    current = make_review(
        "current",
        offset_minutes=5,
        vulnerabilities=[make_vulnerability("GHSA-1", version="1.0.1"), make_vulnerability("GHSA-3")],
    )

    comparison = compare_reviews(current, baseline)

    assert [item.vulnerability_id for item in comparison.persisting_vulnerabilities] == ["GHSA-1"]
    assert [item.vulnerability_id for item in comparison.new_vulnerabilities] == ["GHSA-3"]
    assert [item.vulnerability_id for item in comparison.resolved_vulnerabilities] == ["GHSA-2"]


def test_baseline_endpoint_uses_previous_completed_review(client: TestClient):
    form = review_form()
    first = client.post(
        "/api/reviews",
        data=form,
        files={"file": ("sample.zip", make_zip(), "application/zip")},
    ).json()
    wait_for_status(client, first["id"])

    second = client.post(
        "/api/reviews",
        data=form,
        files={"file": ("sample.zip", make_zip(), "application/zip")},
    ).json()
    wait_for_status(client, second["id"])

    response = client.get(f"/api/reviews/{second['id']}/baseline")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["baseline"]["baseline_id"] == first["id"]
    assert [item["title"] for item in body["baseline"]["persisting_findings"]] == ["示例 AI 发现"]
    assert body["baseline"]["new_findings"] == []
    assert body["baseline"]["resolved_findings"] == []


def test_baseline_endpoint_reports_first_review(client: TestClient):
    created = client.post(
        "/api/reviews",
        data=review_form(),
        files={"file": ("sample.zip", make_zip(), "application/zip")},
    ).json()
    wait_for_status(client, created["id"])

    response = client.get(f"/api/reviews/{created['id']}/baseline")

    assert response.status_code == 200
    body = response.json()
    assert body["baseline"] is None
    assert "首次体检" in body["message"]


def test_baseline_endpoint_rejects_unknown_review(client: TestClient):
    assert client.get("/api/reviews/missing/baseline").status_code == 404
