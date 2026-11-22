from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..models import SkippedFile
from .inventory import MAX_SKIPPED_DETAILS, SourceFile


@dataclass(frozen=True, slots=True)
class CodeChunk:
    file_path: str
    language: str
    start_line: int
    end_line: int
    content: str
    file_paths: tuple[str, ...] = ()
    complete_paths: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        paths = self.file_paths or (self.file_path,)
        if len(paths) == 1:
            return paths[0]
        return f"{paths[0]} 等 {len(paths)} 个文件"


@dataclass(slots=True)
class ChunkResult:
    chunks: list[CodeChunk]
    skipped: list[SkippedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _split_file(
    source_file: SourceFile,
    content: str,
    *,
    max_chars: int,
    overlap_lines: int,
) -> list[CodeChunk]:
    """按字符预算切分单个文件，保留少量重叠行以便跨块阅读上下文。"""
    segments: list[CodeChunk] = []
    lines = content.splitlines(keepends=True)
    if not lines:
        return [CodeChunk(source_file.path, source_file.language, 1, 1, "")]
    start = 0
    while start < len(lines):
        if len(lines[start]) > max_chars:
            long_line = lines[start]
            for offset in range(0, len(long_line), max_chars):
                segments.append(
                    CodeChunk(
                        source_file.path,
                        source_file.language,
                        start + 1,
                        start + 1,
                        long_line[offset : offset + max_chars],
                    )
                )
            start += 1
            continue
        size = 0
        end = start
        while end < len(lines) and size + len(lines[end]) <= max_chars:
            size += len(lines[end])
            end += 1
        segments.append(
            CodeChunk(
                file_path=source_file.path,
                language=source_file.language,
                start_line=start + 1,
                end_line=end,
                content="".join(lines[start:end]),
            )
        )
        if end >= len(lines):
            break
        start = max(start + 1, end - overlap_lines)
    return segments


def _merge(segments: list[CodeChunk]) -> CodeChunk:
    if len(segments) == 1:
        return segments[0]
    parts = [
        f"===== 文件 {segment.file_path}（第 {segment.start_line}-{segment.end_line} 行）=====\n"
        f"{segment.content}"
        for segment in segments
    ]
    first = segments[0]
    return CodeChunk(
        file_path=first.file_path,
        language=first.language,
        start_line=first.start_line,
        end_line=first.end_line,
        content="\n\n".join(parts),
        file_paths=tuple(segment.file_path for segment in segments),
    )


def chunk_files(
    files: list[SourceFile],
    *,
    max_chars: int = 20_000,
    overlap_lines: int = 4,
    max_chunks: int = 149,
    exclude_files: set[str] | frozenset[str] | None = None,
) -> ChunkResult:
    if max_chars < 100:
        raise ValueError("max_chars must allow useful code chunks")
    result = ChunkResult(chunks=[])
    excluded = exclude_files or frozenset()
    per_file: list[tuple[SourceFile, list[CodeChunk]]] = []

    for source_file in files:
        if source_file.path in excluded:
            continue
        try:
            content = source_file.read_text()
        except (OSError, UnicodeDecodeError):
            result.skipped.append(SkippedFile(path=source_file.path, reason="分块时无法读取"))
            continue
        per_file.append(
            (
                source_file,
                _split_file(
                    source_file,
                    content,
                    max_chars=max_chars,
                    overlap_lines=overlap_lines,
                ),
            )
        )

    # 广度优先：先让尽可能多的文件拿到首个片段，再追加后续片段。
    ordered: list[CodeChunk] = []
    depth = 0
    while True:
        progressed = False
        for _, segments in per_file:
            if depth < len(segments):
                ordered.append(segments[depth])
                progressed = True
        if not progressed:
            break
        depth += 1

    # 把多个小文件片段打包进同一次请求，用满字符预算以提高文件覆盖数。
    packed: list[CodeChunk] = []
    current: list[CodeChunk] = []
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            packed.append(_merge(current))
            current = []
            current_size = 0

    for segment in ordered:
        header = 0 if not current else len(segment.file_path) + 40
        cost = len(segment.content) + header
        if current and (
            current_size + cost > max_chars
            or any(item.file_path == segment.file_path for item in current)
        ):
            flush()
            cost = len(segment.content)
        current.append(segment)
        current_size += cost
    flush()

    selected = packed[:max_chunks]
    segment_counts = {source_file.path: len(segments) for source_file, segments in per_file}
    selected = [
        replace(
            chunk,
            complete_paths=tuple(
                path
                for path in (chunk.file_paths or (chunk.file_path,))
                if segment_counts.get(path) == 1
            ),
        )
        for chunk in selected
    ]
    covered: dict[str, int] = {}
    for chunk in selected:
        for path in chunk.file_paths or (chunk.file_path,):
            covered[path] = covered.get(path, 0) + 1

    partial_files = 0
    uncovered: list[str] = []
    for source_file, segments in per_file:
        included = covered.get(source_file.path, 0)
        if included == 0:
            uncovered.append(source_file.path)
        elif included < len(segments):
            partial_files += 1
    result.chunks = selected
    for path in uncovered[:MAX_SKIPPED_DETAILS]:
        result.skipped.append(
            SkippedFile(path=path, reason="AI 请求预算不足，未发送分析")
        )
    if len(uncovered) > MAX_SKIPPED_DETAILS:
        result.skipped.append(
            SkippedFile(
                path="（其余文件）",
                reason=f"另有 {len(uncovered) - MAX_SKIPPED_DETAILS} 个文件因 AI 请求预算不足未分析",
            )
        )

    if partial_files:
        result.warnings.append(
            f"AI 请求预算仅覆盖了 {partial_files} 个文件的部分内容，报告未包含这些文件的全部代码。"
        )
    if result.skipped:
        result.warnings.append("分析受文件读取或 AI 请求预算限制，报告不覆盖全部代码。")
    return result
