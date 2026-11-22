from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Coverage, Finding, Review, ReviewSummary, Vulnerability


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class Base(DeclarativeBase):
    pass


class ReviewRecord(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    stage_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    vulnerabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dependency_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    awaiting_decision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ai_budget: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ai_base_url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    ai_model: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    covered_files: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    def _base_values(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "status": self.status,
            "stage": self.stage,
            "stage_detail": self.stage_detail,
            "progress": self.progress,
            "error": self.error,
            "created_at": aware(self.created_at),
            "updated_at": aware(self.updated_at),
            "completed_at": aware(self.completed_at),
            "coverage": Coverage.model_validate(self.coverage or {}),
            "warnings": list(self.warnings or []),
            "dependency_status": self.dependency_status,
            "awaiting_decision": bool(self.awaiting_decision),
            "depth": self.depth or 1,
            "ai_budget": self.ai_budget or 0,
            "ai_base_url": self.ai_base_url or "",
            "ai_model": self.ai_model or "",
        }

    def to_review(self) -> Review:
        return Review(
            **self._base_values(),
            findings=[Finding.model_validate(item) for item in (self.findings or [])],
            vulnerabilities=[
                Vulnerability.model_validate(item) for item in (self.vulnerabilities or [])
            ],
        )

    def to_summary(self) -> ReviewSummary:
        return ReviewSummary(**self._base_values())


class ReviewStore:
    def __init__(self, database_url: str):
        if database_url.startswith("sqlite:///"):
            database_path = database_url.removeprefix("sqlite:///")
            if database_path != ":memory:":
                Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
            poolclass=StaticPool if database_url.endswith(":memory:") else None,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, class_=Session)
        Base.metadata.create_all(self.engine)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """为既有 SQLite 数据库补齐新增列，避免升级后无法读取历史记录。"""
        if not self.engine.url.drivername.startswith("sqlite"):
            return
        additions = {
            "awaiting_decision": "BOOLEAN NOT NULL DEFAULT 0",
            "depth": "INTEGER NOT NULL DEFAULT 1",
            "ai_budget": "INTEGER NOT NULL DEFAULT 0",
            "ai_base_url": "VARCHAR(2000) NOT NULL DEFAULT ''",
            "ai_model": "VARCHAR(300) NOT NULL DEFAULT ''",
            "covered_files": "JSON NOT NULL DEFAULT '[]'",
        }
        with self.engine.begin() as connection:
            existing = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(reviews)")
            }
            for name, ddl in additions.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE reviews ADD COLUMN {name} {ddl}")

    def create(
        self,
        source_type: str,
        source_name: str,
        review_id: str | None = None,
        *,
        ai_budget: int = 0,
        ai_base_url: str = "",
        ai_model: str = "",
    ) -> Review:
        now = utcnow()
        record = ReviewRecord(
            id=review_id or str(uuid.uuid4()),
            source_type=source_type,
            source_name=source_name,
            status="queued",
            stage="queued",
            stage_detail="等待审查",
            progress=0,
            created_at=now,
            updated_at=now,
            coverage=Coverage().model_dump(),
            findings=[],
            vulnerabilities=[],
            warnings=[],
            dependency_status="pending",
            awaiting_decision=False,
            depth=1,
            ai_budget=ai_budget,
            ai_base_url=ai_base_url,
            ai_model=ai_model,
            covered_files=[],
        )
        with self.sessions.begin() as session:
            session.add(record)
        return record.to_review()

    def get_record(self, review_id: str) -> ReviewRecord | None:
        with self.sessions() as session:
            record = session.get(ReviewRecord, review_id)
            if record is not None:
                session.expunge(record)
            return record

    def get(self, review_id: str) -> Review | None:
        record = self.get_record(review_id)
        return record.to_review() if record else None

    def list(self) -> list[ReviewSummary]:
        with self.sessions() as session:
            records = session.scalars(
                select(ReviewRecord).order_by(ReviewRecord.created_at.desc())
            ).all()
            return [record.to_summary() for record in records]

    def previous_completed(
        self,
        review_id: str,
        source_type: str,
        source_name: str,
        created_at: datetime,
    ) -> Review | None:
        """查找同一目标在本次之前最近一次完成的审查，作为基线。"""
        with self.sessions() as session:
            record = session.scalars(
                select(ReviewRecord)
                .where(
                    ReviewRecord.id != review_id,
                    ReviewRecord.source_type == source_type,
                    ReviewRecord.source_name == source_name,
                    ReviewRecord.status == "completed",
                    ReviewRecord.created_at < created_at,
                )
                .order_by(ReviewRecord.created_at.desc())
                .limit(1)
            ).first()
            return record.to_review() if record else None

    def update(self, review_id: str, **values: Any) -> Review | None:
        values["updated_at"] = utcnow()
        with self.sessions.begin() as session:
            record = session.get(ReviewRecord, review_id)
            if record is None:
                return None
            for key, value in values.items():
                if isinstance(value, Coverage):
                    value = value.model_dump()
                elif key == "findings" and isinstance(value, list):
                    value = [item.model_dump(mode="json") if isinstance(item, Finding) else item for item in value]
                elif key == "vulnerabilities" and isinstance(value, list):
                    value = [
                        item.model_dump(mode="json") if isinstance(item, Vulnerability) else item
                        for item in value
                    ]
                setattr(record, key, value)
            session.flush()
            session.expunge(record)
        return record.to_review()

    def cancel_active(self, review_id: str) -> tuple[Review | None, bool]:
        now = utcnow()
        with self.sessions.begin() as session:
            record = session.get(ReviewRecord, review_id)
            if record is None:
                return None, False
            if record.status not in {"queued", "running"}:
                return record.to_review(), False
            record.status = "cancelled"
            record.stage = "cancelled"
            record.stage_detail = "正在取消并清理临时源码"
            record.error = None
            record.awaiting_decision = False
            record.updated_at = now
            record.completed_at = now
            session.flush()
            session.expunge(record)
        return record.to_review(), True

    def recover_interrupted(self) -> int:
        now = utcnow()
        count = 0
        with self.sessions.begin() as session:
            records = session.scalars(
                select(ReviewRecord).where(ReviewRecord.status.in_(["queued", "running"]))
            ).all()
            for record in records:
                record.status = "interrupted"
                record.stage = "interrupted"
                record.stage_detail = "应用重启，任务已中断"
                record.error = "应用重启，未完成的审查已中断，请重新发起。"
                record.awaiting_decision = False
                record.updated_at = now
                record.completed_at = now
                count += 1
        return count

    def retained_source_ids(self) -> set[str]:
        """返回仍需要保留临时源码的任务，供应用启动时清理其余目录。"""
        with self.sessions() as session:
            rows = session.scalars(
                select(ReviewRecord.id).where(
                    ReviewRecord.awaiting_decision.is_(True),
                    ReviewRecord.status == "completed",
                )
            ).all()
            return set(rows)

    def close(self) -> None:
        self.engine.dispose()
