from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SourceType = Literal["github", "zip"]
ReviewStatus = Literal[
    "queued", "running", "completed", "failed", "cancelled", "interrupted"
]
FindingSource = Literal["static", "ai"]
FindingCategory = Literal["bug", "design", "security", "maintainability"]
Severity = Literal["critical", "high", "medium", "low", "info"]
DependencyStatus = Literal["pending", "completed", "unavailable"]


class SkippedFile(BaseModel):
    path: str
    reason: str


class Coverage(BaseModel):
    total_files: int = 0
    eligible_files: int = 0
    analyzed_files: int = 0
    static_scanned_files: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    skipped_files: list[SkippedFile] = Field(default_factory=list)


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    source: FindingSource
    category: FindingCategory
    severity: Severity
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=8000)
    suggestion: str = Field(min_length=1, max_length=8000)
    file_path: str = Field(min_length=1, max_length=1000)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    evidence: str = Field(min_length=1, max_length=12000)
    requires_human_review: bool = True


class Vulnerability(BaseModel):
    package_name: str
    package_version: str
    ecosystem: str
    vulnerability_id: str
    summary: str = ""
    severity: str = "unknown"
    references: list[str] = Field(default_factory=list)


class ReviewBase(BaseModel):
    id: str
    source_type: SourceType
    source_name: str
    status: ReviewStatus
    stage: str
    stage_detail: str = ""
    progress: int = Field(ge=0, le=100)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    coverage: Coverage = Field(default_factory=Coverage)
    warnings: list[str] = Field(default_factory=list)
    dependency_status: DependencyStatus = "pending"
    awaiting_decision: bool = False
    depth: int = Field(default=1, ge=1)
    ai_budget: int = Field(default=0, ge=0)
    ai_base_url: str = ""
    ai_model: str = ""
    disclaimer: str = (
        "静态与 AI 审查均为辅助判断，需人工确认；扫描覆盖范围不构成全量或零误报保证。"
    )


class ReviewSummary(ReviewBase):
    pass


class Review(ReviewBase):
    findings: list[Finding] = Field(default_factory=list)
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def findings_have_ordered_lines(cls, findings: list[Finding]) -> list[Finding]:
        for finding in findings:
            if finding.line_end < finding.line_start:
                raise ValueError("line_end must not precede line_start")
        return findings


class BaselineComparison(BaseModel):
    current_id: str
    current_created_at: datetime
    baseline_id: str
    baseline_created_at: datetime
    new_findings: list[Finding] = Field(default_factory=list)
    resolved_findings: list[Finding] = Field(default_factory=list)
    persisting_findings: list[Finding] = Field(default_factory=list)
    new_vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    resolved_vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    persisting_vulnerabilities: list[Vulnerability] = Field(default_factory=list)


class BaselineResponse(BaseModel):
    baseline: BaselineComparison | None = None
    message: str | None = None
