from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Hashable, TypeVar

from ..models import BaselineComparison, Finding, Review, Vulnerability


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_NOISE = re.compile(r"[\s\u3000:：,，.。;；、\-_/\\()（）\[\]【】<>《》'\"“”‘’]+")

T = TypeVar("T")


def _normalize_text(value: str) -> str:
    return _NOISE.sub("", (value or "").lower())


def finding_key(finding: Finding) -> tuple[str, str, str, str]:
    """跨轮次匹配的指纹：来源、类别、文件与标题；不依赖会漂移的行号。"""
    return (
        finding.source,
        finding.category,
        finding.file_path.strip().replace("\\", "/").lower(),
        _normalize_text(finding.title),
    )


def vulnerability_key(vulnerability: Vulnerability) -> tuple[str, str, str]:
    return (
        vulnerability.ecosystem.strip().lower(),
        vulnerability.package_name.strip().lower(),
        vulnerability.vulnerability_id.strip().lower(),
    )


def _split(
    current: list[T],
    baseline: list[T],
    key: Callable[[T], Hashable],
) -> tuple[list[T], list[T], list[T]]:
    """按指纹一一配对，返回（新增、已修复、仍存在）。"""
    buckets: dict[Hashable, list[T]] = defaultdict(list)
    for item in baseline:
        buckets[key(item)].append(item)

    new_items: list[T] = []
    persisting: list[T] = []
    for item in current:
        bucket = buckets.get(key(item))
        if bucket:
            bucket.pop(0)
            persisting.append(item)
        else:
            new_items.append(item)
    resolved = [item for bucket in buckets.values() for item in bucket]
    return new_items, resolved, persisting


def _finding_order(finding: Finding) -> tuple[int, str, int, str]:
    return (
        _SEVERITY_ORDER.get(finding.severity, len(_SEVERITY_ORDER)),
        finding.file_path.lower(),
        finding.line_start,
        finding.title,
    )


def _vulnerability_order(vulnerability: Vulnerability) -> tuple[str, str]:
    return (vulnerability.package_name.lower(), vulnerability.vulnerability_id.lower())


def compare_reviews(current: Review, baseline: Review) -> BaselineComparison:
    new_findings, resolved_findings, persisting_findings = _split(
        current.findings, baseline.findings, finding_key
    )
    new_vulnerabilities, resolved_vulnerabilities, persisting_vulnerabilities = _split(
        current.vulnerabilities, baseline.vulnerabilities, vulnerability_key
    )
    return BaselineComparison(
        current_id=current.id,
        current_created_at=current.created_at,
        baseline_id=baseline.id,
        baseline_created_at=baseline.created_at,
        new_findings=sorted(new_findings, key=_finding_order),
        resolved_findings=sorted(resolved_findings, key=_finding_order),
        persisting_findings=sorted(persisting_findings, key=_finding_order),
        new_vulnerabilities=sorted(new_vulnerabilities, key=_vulnerability_order),
        resolved_vulnerabilities=sorted(resolved_vulnerabilities, key=_vulnerability_order),
        persisting_vulnerabilities=sorted(persisting_vulnerabilities, key=_vulnerability_order),
    )
