from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import ReviewStore, utcnow
from .models import Coverage, Finding
from .services.ai_review import OpenAICompatibleReviewer
from .services.chunker import chunk_files
from .services.ingest import SourceIngestError, clone_github, remove_tree, safe_extract_zip
from .services.inventory import build_inventory
from .services.osv_scan import OSVScanner
from .services.static_scan import scan_static


@dataclass(slots=True)
class ReviewJob:
    review_id: str
    source_type: str
    source_name: str
    base_url: str
    api_key: str
    model: str
    repo_url: str | None = None
    upload_path: Path | None = None
    mode: str = "initial"
    max_requests: int = 0
    cancel_event: asyncio.Event | None = None

    def __post_init__(self) -> None:
        if self.cancel_event is None:
            self.cancel_event = asyncio.Event()


AIReviewerFactory = Callable[[ReviewJob], Any]
OSVScannerFactory = Callable[[], Any]


class ReviewWorker:
    def __init__(
        self,
        store: ReviewStore,
        settings: Settings,
        *,
        ai_reviewer_factory: AIReviewerFactory | None = None,
        osv_scanner_factory: OSVScannerFactory | None = None,
    ):
        self.store = store
        self.settings = settings
        self.ai_reviewer_factory = ai_reviewer_factory or self._default_ai_factory
        self.osv_scanner_factory = osv_scanner_factory or (
            lambda: OSVScanner(
                timeout_seconds=settings.osv_timeout_seconds,
                max_retries=settings.osv_max_retries,
            )
        )
        self.queue: asyncio.Queue[ReviewJob] = asyncio.Queue()
        self.jobs: dict[str, ReviewJob] = {}
        self.current_job: ReviewJob | None = None
        self.current_task: asyncio.Task[None] | None = None
        self.runner: asyncio.Task[None] | None = None
        self.stopping = False

    def _default_ai_factory(self, job: ReviewJob) -> OpenAICompatibleReviewer:
        return OpenAICompatibleReviewer(
            base_url=job.base_url,
            api_key=job.api_key,
            model=job.model,
            timeout_seconds=self.settings.ai_timeout_seconds,
            max_retries=self.settings.ai_max_retries,
            max_requests=job.max_requests or self.settings.max_ai_requests,
        )

    async def start(self) -> None:
        if self.runner is None or self.runner.done():
            self.stopping = False
            self.runner = asyncio.create_task(self._run_loop())

    async def enqueue(self, job: ReviewJob) -> None:
        await self.start()
        self.jobs[job.review_id] = job
        await self.queue.put(job)

    async def cancel(self, review_id: str) -> bool:
        job = self.jobs.get(review_id)
        if job is None:
            return False
        if job.cancel_event is not None:
            job.cancel_event.set()
        if self.current_job and self.current_job.review_id == review_id:
            if self.current_task and not self.current_task.done():
                self.current_task.cancel()
        return True

    async def _run_loop(self) -> None:
        while True:
            job = await self.queue.get()
            self.current_job = job
            try:
                review = self.store.get(job.review_id)
                if review is None or review.status in {"cancelled", "interrupted"}:
                    await self._cleanup(job)
                    continue
                self.current_task = asyncio.create_task(self._execute(job))
                try:
                    await self.current_task
                except asyncio.CancelledError:
                    if self.stopping:
                        raise
                    self.store.cancel_active(job.review_id)
                    await self._cleanup(job)
            finally:
                job.api_key = ""
                self.jobs.pop(job.review_id, None)
                self.current_job = None
                self.current_task = None
                self.queue.task_done()

    def _check_cancelled(self, job: ReviewJob) -> None:
        if job.cancel_event is not None and job.cancel_event.is_set():
            raise asyncio.CancelledError

    def _update(self, review_id: str, **values: Any) -> None:
        self.store.update(review_id, **values)

    async def _execute(self, job: ReviewJob) -> None:
        review_id = job.review_id
        work_dir = self.settings.sources_dir / review_id
        repository = work_dir / "repo"
        keep_source = False
        try:
            if job.mode == "continue":
                keep_source = await self._execute_continue(job, repository)
                return
            self._check_cancelled(job)
            self._update(
                review_id,
                status="running",
                stage="ingest",
                stage_detail="正在安全接入源码",
                progress=5,
                error=None,
                completed_at=None,
            )
            work_dir.mkdir(parents=True, exist_ok=True)
            if job.source_type == "github":
                if not job.repo_url:
                    raise SourceIngestError("缺少 GitHub 仓库地址。")
                await clone_github(
                    job.repo_url,
                    repository,
                    timeout_seconds=self.settings.github_timeout_seconds,
                    max_bytes=self.settings.github_max_bytes,
                )
            else:
                if not job.upload_path:
                    raise SourceIngestError("缺少 ZIP 上传文件。")
                try:
                    await asyncio.to_thread(safe_extract_zip, job.upload_path, repository)
                finally:
                    job.upload_path.unlink(missing_ok=True)
            self._check_cancelled(job)

            self._update(
                review_id,
                stage="inventory",
                stage_detail="正在筛选清单并统计覆盖范围",
                progress=20,
            )
            inventory = await asyncio.to_thread(build_inventory, repository)
            self._check_cancelled(job)
            chunks = chunk_files(
                inventory.files,
                max_chunks=max(1, self.settings.max_ai_requests - 1),
            )
            coverage = inventory.coverage.model_copy(deep=True)
            # 该字段表示真正被 AI 审查到的文件数，完成 AI 阶段后再回填。
            coverage.analyzed_files = 0
            coverage.skipped_files.extend(chunks.skipped)
            self._update(review_id, coverage=coverage)

            self._update(
                review_id,
                stage="static_scan",
                stage_detail="正在执行启发式静态规则",
                progress=35,
            )
            static_findings = await asyncio.to_thread(scan_static, inventory.candidates)
            coverage.static_scanned_files = len(inventory.candidates)
            self._update(review_id, coverage=coverage, findings=static_findings)
            self._check_cancelled(job)

            self._update(
                review_id,
                stage="ai_review",
                stage_detail="正在执行文件/模块 AI 审查",
                progress=45,
            )
            warnings = list(chunks.warnings)
            ai_findings = []
            ai_complete_files: set[str] = set()
            try:
                reviewer = self.ai_reviewer_factory(job)

                async def ai_progress(completed: int, total: int) -> None:
                    self._check_cancelled(job)
                    percentage = 45 + (35 * completed // max(1, total))
                    self._update(
                        review_id,
                        stage_detail=f"AI 模块审查 {completed}/{total}",
                        progress=percentage,
                    )

                ai_result = await reviewer.review(
                    inventory,
                    chunks.chunks,
                    cancel_event=job.cancel_event,
                    progress_callback=ai_progress,
                )
                ai_findings = ai_result.findings
                coverage.analyzed_files = len(ai_result.covered_files)
                ai_complete_files = set(ai_result.complete_files)
                warnings.extend(ai_result.warnings)
            except asyncio.CancelledError:
                raise
            except Exception:
                warnings.append("AI 审查不可用，已保留静态扫描结果；请检查服务配置后重试。")
            self._check_cancelled(job)
            all_findings = static_findings + ai_findings
            self._update(
                review_id,
                coverage=coverage,
                findings=all_findings,
                warnings=warnings,
            )

            self._update(
                review_id,
                stage="dependency_scan",
                stage_detail="正在独立查询 OSV 依赖漏洞",
                progress=85,
            )
            vulnerabilities = []
            dependency_status = "unavailable"
            try:
                dependency_result = await self.osv_scanner_factory().scan(repository)
                dependency_status = dependency_result.status
                vulnerabilities = dependency_result.vulnerabilities
                warnings.extend(dependency_result.warnings)
            except asyncio.CancelledError:
                raise
            except Exception:
                warnings.append("OSV 依赖扫描不可用，不影响代码审查结果。")
            self._check_cancelled(job)

            self._update(
                review_id,
                stage="finalizing",
                stage_detail="正在校验并汇总结构化报告",
                progress=95,
                warnings=warnings,
                dependency_status=dependency_status,
            )
            await asyncio.to_thread(self.store.get, review_id)
            self._check_cancelled(job)
            keep_source = True
            self._update(
                review_id,
                status="completed",
                stage="completed",
                stage_detail="审查完成；可继续深入审查或结束审查",
                progress=100,
                error=None,
                findings=all_findings,
                vulnerabilities=vulnerabilities,
                warnings=list(dict.fromkeys(warnings)),
                dependency_status=dependency_status,
                awaiting_decision=True,
                ai_budget=job.max_requests or self.settings.max_ai_requests,
                covered_files=sorted(ai_complete_files),
                completed_at=utcnow(),
            )
        except asyncio.CancelledError:
            if self.stopping:
                review = self.store.get(review_id)
                if review and review.status in {"queued", "running"}:
                    self._update(
                        review_id,
                        status="interrupted",
                        stage="interrupted",
                        stage_detail="应用停止，任务已中断",
                        error="应用停止，未完成的审查已中断，请重新发起。",
                        awaiting_decision=False,
                        completed_at=utcnow(),
                    )
            else:
                self.store.cancel_active(review_id)
        except SourceIngestError as exc:
            self._update(
                review_id,
                status="failed",
                stage="failed",
                stage_detail="源码接入失败",
                error=str(exc),
                awaiting_decision=False,
                completed_at=utcnow(),
            )
        except Exception:
            self._update(
                review_id,
                status="failed",
                stage="failed",
                stage_detail="审查失败；临时源码已清理",
                error="审查任务未能完成，请检查输入和本地服务配置后重试。",
                awaiting_decision=False,
                completed_at=utcnow(),
            )
        finally:
            if not keep_source:
                await self._cleanup(job)

    async def _execute_continue(self, job: ReviewJob, repository: Path) -> bool:
        review_id = job.review_id
        record = self.store.get_record(review_id)
        if record is None:
            raise SourceIngestError("审查任务不存在。")
        if not repository.is_dir():
            raise SourceIngestError("临时源码已被清理，无法继续深入审查，请重新发起审查。")
        self._check_cancelled(job)
        self._update(
            review_id,
            status="running",
            stage="inventory",
            stage_detail="正在重建源码清单",
            progress=8,
            error=None,
            completed_at=None,
            awaiting_decision=False,
        )
        inventory = await asyncio.to_thread(build_inventory, repository)
        self._check_cancelled(job)

        previous_coverage = Coverage.model_validate(record.coverage or {})
        previous_findings = [Finding.model_validate(item) for item in (record.findings or [])]
        covered = set(record.covered_files or [])
        budget = job.max_requests or self.settings.max_ai_requests
        remaining = [item for item in inventory.files if item.path not in covered]
        chunks = chunk_files(remaining, max_chunks=max(1, budget - 1))
        coverage = inventory.coverage.model_copy(deep=True)
        coverage.analyzed_files = max(len(covered), previous_coverage.analyzed_files)
        coverage.static_scanned_files = previous_coverage.static_scanned_files
        coverage.skipped_files = chunks.skipped
        warnings = list(record.warnings or []) + list(chunks.warnings)

        if not remaining:
            self._update(
                review_id,
                status="completed",
                stage="completed",
                stage_detail="所有可分析文件均已完成；可结束审查并清理临时源码",
                progress=100,
                error=None,
                coverage=coverage,
                findings=previous_findings,
                warnings=list(dict.fromkeys(warnings)),
                awaiting_decision=True,
                covered_files=sorted(covered),
                completed_at=utcnow(),
            )
            return True

        self._update(
            review_id,
            stage="ai_review",
            stage_detail=f"第 {record.depth or 1} 轮深入审查：AI 模块分析",
            progress=20,
            coverage=coverage,
        )
        ai_findings: list[Finding] = []
        try:
            reviewer = self.ai_reviewer_factory(job)

            async def ai_progress(completed: int, total: int) -> None:
                self._check_cancelled(job)
                percentage = 20 + (70 * completed // max(1, total))
                self._update(
                    review_id,
                    stage_detail=f"第 {record.depth or 1} 轮深入审查 {completed}/{total}",
                    progress=percentage,
                )

            ai_result = await reviewer.review(
                inventory,
                chunks.chunks,
                cancel_event=job.cancel_event,
                progress_callback=ai_progress,
            )
            ai_findings = ai_result.findings
            covered.update(ai_result.complete_files)
            coverage.analyzed_files = max(len(covered), previous_coverage.analyzed_files)
            warnings.extend(ai_result.warnings)
        except asyncio.CancelledError:
            raise
        except Exception:
            warnings.append("AI 深入审查不可用，已保留上一轮结果。")
        self._check_cancelled(job)

        merged: dict[tuple[str, int, int, str], Finding] = {}
        for finding in previous_findings + ai_findings:
            merged.setdefault(
                (finding.file_path, finding.line_start, finding.line_end, finding.title),
                finding,
            )
        self._update(
            review_id,
            status="completed",
            stage="completed",
            stage_detail="深入审查完成；可继续深入或结束审查",
            progress=100,
            error=None,
            findings=list(merged.values()),
            warnings=list(dict.fromkeys(warnings)),
            coverage=coverage,
            covered_files=sorted(covered),
            awaiting_decision=True,
            completed_at=utcnow(),
        )
        return True

    async def _cleanup(self, job: ReviewJob) -> None:
        work_dir = self.settings.sources_dir / job.review_id
        try:
            await asyncio.to_thread(remove_tree, work_dir)
            if job.upload_path:
                await asyncio.to_thread(job.upload_path.unlink, missing_ok=True)
        except OSError:
            pass

    async def stop(self) -> None:
        self.stopping = True
        current = self.current_task
        current_job = self.current_job
        if current and not current.done():
            current.cancel()
            try:
                await current
            except asyncio.CancelledError:
                review = self.store.get(current_job.review_id) if current_job else None
                if review and review.status in {"queued", "running"}:
                    self.store.update(
                        review.id,
                        status="interrupted",
                        stage="interrupted",
                        error="应用停止，未完成的审查已中断，请重新发起。",
                        completed_at=utcnow(),
                    )
        if self.runner and not self.runner.done():
            self.runner.cancel()
            try:
                await self.runner
            except asyncio.CancelledError:
                pass
        for job in list(self.jobs.values()):
            if job.cancel_event:
                job.cancel_event.set()
            await self._cleanup(job)
            job.api_key = ""
        self.store.recover_interrupted()
        self.jobs.clear()
        self.runner = None
        self.current_task = None
        self.current_job = None
