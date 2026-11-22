from __future__ import annotations

import json
import re
from typing import Literal

from ..models import Finding, Review


def _markdown_text(value: str) -> str:
    return value.replace("\r", " ").replace("\x00", "")


def _finding_markdown(finding: Finding) -> list[str]:
    return [
        f"### [{finding.severity.upper()}] {_markdown_text(finding.title)}",
        "",
        f"- ID：`{finding.id}`",
        f"- 来源：{finding.source}；类别：{finding.category}；需人工确认：是",
        f"- 位置：`{finding.file_path}:{finding.line_start}-{finding.line_end}`",
        f"- 说明：{_markdown_text(finding.description)}",
        f"- 建议：{_markdown_text(finding.suggestion)}",
        "",
        "证据：",
        "",
        "```text",
        finding.evidence.replace("```", "` ` `"),
        "```",
        "",
    ]


def render_markdown(review: Review) -> str:
    coverage = review.coverage
    lines = [
        f"# 代码审查报告：{review.source_name}",
        "",
        f"> {review.disclaimer}",
        "",
        "## 审查信息",
        "",
        f"- 审查 ID：`{review.id}`",
        f"- 来源：{review.source_type} / {review.source_name}",
        f"- 状态：{review.status}；阶段：{review.stage}",
        f"- 创建时间：{review.created_at.isoformat()}",
        f"- 完成时间：{review.completed_at.isoformat() if review.completed_at else '未完成'}",
        "",
        "## 覆盖范围",
        "",
        f"- 清单文件：{coverage.total_files}",
        f"- 可审查源码文件：{coverage.eligible_files or coverage.total_files}",
        f"- 静态规则扫描：{coverage.static_scanned_files}",
        f"- AI 深度分析：{coverage.analyzed_files}",
        f"- 语言：{json.dumps(coverage.languages, ensure_ascii=False)}",
        f"- 依赖扫描：{review.dependency_status}",
        "",
    ]
    if review.error:
        lines.extend([f"**错误：{_markdown_text(review.error)}**", ""])
    if review.warnings:
        lines.extend(["## 警告与限制", ""])
        lines.extend(f"- {_markdown_text(warning)}" for warning in review.warnings)
        lines.append("")
    lines.extend(["## 代码发现", ""])
    if review.findings:
        for finding in review.findings:
            lines.extend(_finding_markdown(finding))
    else:
        lines.extend(["未发现符合规则的代码问题；这不代表代码不存在缺陷。", ""])
    lines.extend(["## 依赖漏洞（独立结果）", ""])
    if review.vulnerabilities:
        for vulnerability in review.vulnerabilities:
            lines.extend(
                [
                    f"### {_markdown_text(vulnerability.package_name)} {vulnerability.package_version}",
                    "",
                    f"- 生态：{vulnerability.ecosystem}",
                    f"- 漏洞：`{vulnerability.vulnerability_id}`；严重度：{vulnerability.severity}",
                    f"- 摘要：{_markdown_text(vulnerability.summary)}",
                ]
            )
            lines.extend(f"- 参考：{reference}" for reference in vulnerability.references)
            lines.append("")
    else:
        lines.extend(["无已返回的依赖漏洞记录。", ""])
    lines.extend(["## 跳过或未覆盖文件", ""])
    if coverage.skipped_files:
        lines.extend(
            f"- `{item.path}`：{_markdown_text(item.reason)}" for item in coverage.skipped_files
        )
    else:
        lines.append("- 无记录")
    lines.append("")
    return "\n".join(lines)


def safe_report_filename(review: Review, report_format: Literal["json", "markdown"]) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", review.source_name).strip(".-")
    stem = stem[:60] or "review"
    extension = "json" if report_format == "json" else "md"
    return f"{stem}-{review.id}.{extension}"


def render_json(review: Review) -> str:
    return json.dumps(review.model_dump(mode="json"), ensure_ascii=False, indent=2)
