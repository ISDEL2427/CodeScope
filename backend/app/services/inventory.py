from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Coverage, SkippedFile


EXCLUDED_DIRECTORIES = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor",
    "venv", ".venv", "__pycache__", "dist", "build", "target", "out",
    ".next", ".nuxt", ".idea", ".vscode", "coverage", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", "obj", "bin",
}
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svgz",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar",
    ".war", ".exe", ".dll", ".so", ".dylib", ".class", ".pyc", ".pyo",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".avi",
    ".mov", ".pdf", ".sqlite", ".db", ".bin", ".wasm", ".p12", ".pfx",
}
LANGUAGE_EXTENSIONS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".go": "Go",
    ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C/C++",
    ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#", ".swift": "Swift", ".scala": "Scala", ".sql": "SQL",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue",
    ".svelte": "Svelte", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".xml": "XML", ".md": "Markdown", ".sql": "SQL",
}
SECRET_NAMES = {
    ".env", ".npmrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials.json", "secrets.json", "service-account.json",
}
GENERATED_SUFFIXES = (
    ".min.js", ".min.css", ".generated.cs", ".designer.cs", ".pb.go",
    ".pb.cc", ".pb.h", "_generated.py", ".g.dart", ".freezed.dart",
)
CODE_LANGUAGES = {
    "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Go", "Rust",
    "Ruby", "PHP", "C", "C/C++", "C++", "C#", "Swift", "Scala", "Shell",
    "PowerShell", "Vue", "Svelte",
}
MAX_SKIPPED_DETAILS = 200


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    absolute_path: Path
    language: str
    size: int
    line_count: int
    characters: int = 0

    def read_text(self) -> str:
        return self.absolute_path.read_text(encoding="utf-8")


@dataclass(slots=True)
class Inventory:
    root: Path
    files: list[SourceFile]
    coverage: Coverage
    directories: list[str]
    total_characters: int
    candidates: list[SourceFile] = field(default_factory=list)


def _skip_reason(path: Path) -> str | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in SECRET_NAMES or name.startswith(".env."):
        return "疑似密钥或本地环境文件"
    if suffix in {".pem", ".key", ".p12", ".pfx", ".keystore", ".kdbx"}:
        return "密钥或证书文件"
    if suffix in BINARY_EXTENSIONS:
        return "二进制、压缩包或媒体文件"
    if name in {"package-lock.json", "poetry.lock", "uv.lock", "yarn.lock", "pnpm-lock.yaml"}:
        return "锁文件仅用于依赖漏洞解析，不发送至 AI"
    if any(name.endswith(item) for item in GENERATED_SUFFIXES):
        return "压缩或生成代码"
    if name.endswith(".min.js") or ".generated." in name or name.startswith("generated_"):
        return "压缩或生成代码"
    return None


def build_inventory(
    root: Path,
    *,
    max_file_bytes: int = 256 * 1024,
    max_files: int = 3000,
    max_characters: int = 8_000_000,
) -> Inventory:
    candidates: list[SourceFile] = []
    skipped: list[SkippedFile] = []
    languages: dict[str, int] = {}
    directories: list[str] = []
    total_files = 0
    omitted = 0
    root = root.resolve()

    def record_skip(path: str, reason: str) -> None:
        nonlocal omitted
        if len(skipped) < MAX_SKIPPED_DETAILS:
            skipped.append(SkippedFile(path=path, reason=reason))
        else:
            omitted += 1

    for base, dirnames, filenames in os.walk(root, followlinks=False):
        base_path = Path(base)
        dirnames[:] = sorted(
            name for name in dirnames
            if name.lower() not in EXCLUDED_DIRECTORIES
            and not (base_path / name).is_symlink()
        )
        for directory in dirnames:
            relative = (base_path / directory).relative_to(root).as_posix()
            directories.append(relative)
        for filename in sorted(filenames):
            absolute = base_path / filename
            relative = absolute.relative_to(root).as_posix()
            if absolute.is_symlink():
                record_skip(relative, "符号链接不予分析")
                continue
            if not absolute.is_file():
                continue
            total_files += 1
            reason = _skip_reason(absolute)
            if reason:
                record_skip(relative, reason)
                continue
            language = LANGUAGE_EXTENSIONS.get(absolute.suffix.lower())
            if language is None:
                record_skip(relative, "不支持的语言或文件格式")
                continue
            try:
                size = absolute.stat().st_size
                if size > max_file_bytes:
                    record_skip(relative, "单文件超过 256 KiB 分析预算")
                    continue
                content = absolute.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                record_skip(relative, "非 UTF-8 或二进制文件")
                continue
            except OSError:
                record_skip(relative, "文件无法读取")
                continue
            if "\x00" in content:
                record_skip(relative, "包含 NUL 的二进制文件")
                continue
            candidates.append(
                SourceFile(
                    path=relative,
                    absolute_path=absolute,
                    language=language,
                    size=size,
                    line_count=len(content.splitlines()),
                    characters=len(content),
                )
            )

    # 优先把预算用于程序源码，文档与配置在预算充足时再纳入。
    candidates.sort(key=lambda item: (0 if item.language in CODE_LANGUAGES else 1, item.path))

    files: list[SourceFile] = []
    total_characters = 0
    for item in candidates:
        if len(files) >= max_files:
            record_skip(item.path, "超过单任务文件数分析预算")
            continue
        if total_characters + item.characters > max_characters:
            record_skip(item.path, "超过单任务代码字符分析预算")
            continue
        total_characters += item.characters
        files.append(item)
        languages[item.language] = languages.get(item.language, 0) + 1

    if omitted:
        skipped.append(
            SkippedFile(
                path="（其余文件）",
                reason=f"另有 {omitted} 个文件未逐条列出：不支持的格式、生成文件或超出分析预算",
            )
        )

    coverage = Coverage(
        total_files=total_files,
        eligible_files=len(candidates),
        analyzed_files=len(files),
        languages=dict(sorted(languages.items())),
        skipped_files=skipped,
    )
    return Inventory(
        root=root,
        files=files,
        coverage=coverage,
        directories=sorted(directories),
        total_characters=total_characters,
        candidates=candidates,
    )
