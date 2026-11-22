from __future__ import annotations

from pathlib import Path

from backend.app.services.chunker import chunk_files
from backend.app.services.inventory import SourceFile, build_inventory
from backend.app.services.static_scan import scan_static


def test_inventory_filters_dependencies_binaries_secrets_and_generated(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "src/generated_pb.go").write_text("package generated", encoding="utf-8")
    (tmp_path / ".env").write_text("PASSWORD=secret", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "unknown.xyz").write_text("unknown", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "node_modules/pkg").mkdir(parents=True)
    (tmp_path / "node_modules/pkg/index.js").write_text("ignored", encoding="utf-8")

    result = build_inventory(tmp_path)

    assert [item.path for item in result.files] == ["src/app.py"]
    assert result.coverage.total_files == 6
    assert result.coverage.analyzed_files == 1
    assert result.coverage.languages == {"Python": 1}
    reasons = {item.path: item.reason for item in result.coverage.skipped_files}
    assert ".env" in reasons
    assert "image.png" in reasons
    assert "package-lock.json" in reasons
    assert not any("node_modules" in path for path in reasons)


def test_inventory_enforces_character_and_file_budgets(tmp_path: Path):
    (tmp_path / "a.py").write_text("a" * 100, encoding="utf-8")
    (tmp_path / "b.py").write_text("b" * 100, encoding="utf-8")
    result = build_inventory(tmp_path, max_characters=120, max_files=1)
    assert len(result.files) == 1
    assert len(result.coverage.skipped_files) == 1


def test_inventory_prioritizes_code_over_docs_and_reports_eligible_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("# 文档\n" * 200, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# 规则\n" * 200, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "src/boot.py").write_text("print('boot')\n", encoding="utf-8")

    result = build_inventory(tmp_path, max_files=2)

    assert [item.path for item in result.files] == ["src/boot.py", "src/main.cpp"]
    assert result.coverage.eligible_files == 4
    assert result.coverage.static_scanned_files == 0
    assert result.coverage.total_files == 4


def test_static_rules_and_sensitive_evidence_redaction(tmp_path: Path):
    path = tmp_path / "danger.py"
    path.write_text(
        "api_key = 'sk-live-super-secret-value'\n"
        "eval(user_input)\n"
        "digest = hashlib.md5(data)\n"
        "query = 'SELECT * FROM users WHERE id=' + user_id\n",
        encoding="utf-8",
    )
    source = SourceFile("danger.py", path, "Python", path.stat().st_size, 4)
    findings = scan_static([source])
    titles = {finding.title for finding in findings}
    assert "疑似硬编码密钥" in titles
    assert "潜在危险动态执行" in titles
    assert "使用弱哈希算法" in titles
    assert "疑似 SQL 字符串拼接" in titles
    secret_finding = next(item for item in findings if item.title == "疑似硬编码密钥")
    assert "sk-live-super-secret-value" not in secret_finding.evidence
    assert "[REDACTED]" in secret_finding.evidence
    severity_by_title = {item.title: item.severity for item in findings}
    assert severity_by_title["疑似硬编码密钥"] == "critical"
    assert severity_by_title["潜在危险动态执行"] == "medium"
    assert severity_by_title["使用弱哈希算法"] == "medium"
    assert severity_by_title["疑似 SQL 字符串拼接"] == "medium"
    assert all(item.source == "static" for item in findings)
    assert all(item.requires_human_review == (item.severity == "critical") for item in findings)


def test_chunker_respects_character_budget_overlap_and_request_limit(tmp_path: Path):
    path = tmp_path / "many.py"
    path.write_text("".join(f"line-{index:03d} = {index}\n" for index in range(40)), encoding="utf-8")
    source = SourceFile("many.py", path, "Python", path.stat().st_size, 40)
    result = chunk_files([source], max_chars=100, overlap_lines=1, max_chunks=3)
    assert 0 < len(result.chunks) <= 3
    assert all(len(chunk.content) <= 100 for chunk in result.chunks)
    assert result.chunks[1].start_line <= result.chunks[0].end_line
    assert not result.skipped
    assert any("部分内容" in warning for warning in result.warnings)


def test_chunker_packs_small_files_and_marks_uncovered_by_budget(tmp_path: Path):
    files = []
    for name in ("a.py", "b.py", "c.py"):
        path = tmp_path / name
        path.write_text(f"{name[0]} = 1\n", encoding="utf-8")
        files.append(SourceFile(name, path, "Python", 6, 1, 6))

    result = chunk_files(files, max_chars=100, max_chunks=1)

    assert len(result.chunks) == 1
    assert result.chunks[0].file_paths == ("a.py", "b.py")
    assert "a = 1" in result.chunks[0].content and "b = 1" in result.chunks[0].content
    assert [item.path for item in result.skipped] == ["c.py"]
    assert result.skipped[0].reason.startswith("AI 请求预算不足")


def test_chunker_splits_oversized_single_line_without_exceeding_budget(tmp_path: Path):
    path = tmp_path / "long.py"
    path.write_text("x" * 250, encoding="utf-8")
    source = SourceFile("long.py", path, "Python", 250, 1)
    result = chunk_files([source], max_chars=100, max_chunks=5)
    assert [len(chunk.content) for chunk in result.chunks] == [100, 100, 50]
    assert all(chunk.start_line == chunk.end_line == 1 for chunk in result.chunks)
