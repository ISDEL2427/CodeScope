from __future__ import annotations

import hashlib
import re

from ..models import Finding
from ..security import redact_sensitive_text
from .inventory import SourceFile


RULES: tuple[tuple[str, re.Pattern[str], str, str, str, str], ...] = (
    (
        "hardcoded-secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd|secret)"
            r"\b\s*[:=]\s*['\"]([^'\"]{8,})['\"]"
        ),
        "critical",
        "疑似硬编码密钥",
        "启发式规则发现代码中可能存在硬编码凭据；证据已脱敏，需人工确认。",
        "立即轮换可能泄露的凭据，改用环境变量或安全的密钥管理机制。",
    ),
    (
        "dangerous-execution",
        re.compile(
            r"\b(?:eval|exec)\s*\(|\bos\.system\s*\(|\bsubprocess\.(?:run|Popen|call|check_output)\s*\([^\n]*shell\s*=\s*True|\bchild_process\.exec\s*\(|\bpickle\.loads\s*\("
        ),
        "medium",
        "潜在危险动态执行",
        "启发式规则发现动态代码执行、Shell 或不安全反序列化调用；是否可利用取决于输入来源，需人工确认。",
        "避免执行不可信输入；使用安全 API、参数数组及明确允许列表。",
    ),
    (
        "weak-hash",
        re.compile(r"(?i)(?:hashlib\.(?:md5|sha1)\s*\(|createHash\s*\(\s*['\"](?:md5|sha1)['\"]\s*\)|MessageDigest\.getInstance\s*\(\s*['\"](?:MD5|SHA-1)['\"])"),
        "medium",
        "使用弱哈希算法",
        "启发式规则发现 MD5 或 SHA-1；若用于密码、签名或完整性保护则存在安全风险。",
        "密码使用 Argon2、bcrypt 或 scrypt；完整性及签名场景使用 SHA-256 或更强算法。",
    ),
    (
        "sql-concatenation",
        re.compile(
            r"(?i)(?:SELECT|INSERT|UPDATE|DELETE)\b[^\n]*(?:\+\s*[A-Za-z_{]|f['\"]|\.format\s*\(|%\s*\()|(?:execute|query)\s*\([^\n]*\+"
        ),
        "medium",
        "疑似 SQL 字符串拼接",
        "启发式规则发现 SQL 语句可能拼接变量；仅当变量可控时才构成注入，需人工确认。",
        "使用参数化查询或 ORM 绑定参数，不要将外部输入直接拼入 SQL。",
    ),
)
_PLACEHOLDERS = {"password", "your_password", "your-api-key", "changeme", "example", "dummy", "test1234", "[redacted]"}


def scan_static(files: list[SourceFile], *, max_findings: int = 500) -> list[Finding]:
    findings: list[Finding] = []
    for source_file in files:
        try:
            content = source_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule_id, pattern, severity, title, description, suggestion in RULES:
                match = pattern.search(line)
                if not match:
                    continue
                if rule_id == "hardcoded-secret":
                    candidate = match.group(1).strip().lower()
                    if candidate in _PLACEHOLDERS or set(candidate) <= {"x", "*", "_", "-"}:
                        continue
                stable = hashlib.sha256(
                    f"{source_file.path}:{line_number}:{rule_id}".encode("utf-8")
                ).hexdigest()[:20]
                findings.append(
                    Finding(
                        id=f"static-{stable}",
                        source="static",
                        category="security",
                        severity=severity,
                        title=title,
                        description=description,
                        suggestion=suggestion,
                        file_path=source_file.path,
                        line_start=line_number,
                        line_end=line_number,
                        evidence=redact_sensitive_text(line.strip())[:4000] or "[空行]",
                        requires_human_review=severity == "critical",
                    )
                )
                if len(findings) >= max_findings:
                    return findings
    return findings
