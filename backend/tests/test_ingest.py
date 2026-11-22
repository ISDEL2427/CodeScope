from __future__ import annotations

import asyncio
import io
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile

from backend.app.services import ingest
from backend.app.services.ingest import (
    SourceIngestError,
    clone_github,
    safe_extract_zip,
    save_upload,
    validate_github_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",
        "https://evil.example/owner/repo",
        "https://github.com.evil.example/owner/repo",
        "https://user:pass@github.com/owner/repo",
        "https://github.com/owner/repo/tree/main",
        "https://github.com/owner/repo?x=1",
        "https://github.com:444/owner/repo",
        "https://github.com/-/repo",
        "https://github.com/owner/..",
        "file:///C:/repo",
    ],
)
def test_rejects_unsafe_github_urls(url: str):
    with pytest.raises(SourceIngestError):
        validate_github_url(url)


def test_normalizes_standard_github_url():
    source = validate_github_url("https://www.github.com/owner/repository.git/")
    assert source.url == "https://github.com/owner/repository.git"
    assert source.name == "owner/repository"


def write_archive(path: Path, entries: list[tuple[str, str, int | None]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.external_attr = mode << 16
            bundle.writestr(info, content)


def test_zip_extracts_regular_files_individually(tmp_path: Path):
    archive = tmp_path / "safe.zip"
    write_archive(archive, [("src/app.py", "print('ok')", None), ("README.md", "hello", None)])
    destination = tmp_path / "out"
    safe_extract_zip(archive, destination)
    assert (destination / "src/app.py").read_text() == "print('ok')"
    assert (destination / "README.md").read_text() == "hello"


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "C:/drive.txt", "a/../../b"])
def test_zip_rejects_traversal_absolute_and_drive_paths(tmp_path: Path, name: str):
    archive = tmp_path / "bad.zip"
    write_archive(archive, [(name, "bad", None)])
    destination = tmp_path / "out"
    with pytest.raises(SourceIngestError):
        safe_extract_zip(archive, destination)
    assert not destination.exists()
    assert not (tmp_path / "outside.txt").exists()


def test_zip_rejects_symlink_and_special_file(tmp_path: Path):
    for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
        archive = tmp_path / "link.zip"
        write_archive(archive, [("link", "target", mode)])
        with pytest.raises(SourceIngestError):
            safe_extract_zip(archive, tmp_path / "out")


def test_zip_rejects_abnormal_compression_ratio(tmp_path: Path):
    archive = tmp_path / "bomb.zip"
    write_archive(archive, [("large.txt", "0" * (1024 * 1024), None)])
    with pytest.raises(SourceIngestError, match="压缩比"):
        safe_extract_zip(archive, tmp_path / "out")


@pytest.mark.asyncio
async def test_upload_limit_is_enforced_while_streaming(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_UPLOAD_BYTES", 10)
    upload = UploadFile(file=io.BytesIO(b"12345678901"), filename="large.zip")
    destination = tmp_path / "upload.zip"
    with pytest.raises(SourceIngestError, match="50 MiB"):
        await save_upload(upload, destination)
    assert not destination.exists()


class FakeProcess:
    def __init__(self, returncode=1):
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return b"", b"https://user:secret@example.invalid private failure"

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_git_uses_safe_arguments_and_sanitizes_failure(tmp_path: Path, monkeypatch):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess(returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    destination = tmp_path / "clone"
    with pytest.raises(SourceIngestError) as error:
        await clone_github("https://github.com/owner/repo", destination)
    assert "secret" not in str(error.value)
    assert "user" not in str(error.value)
    args = captured["args"]
    assert args[0] == "git"
    assert "--depth" in args and "1" in args
    assert "--no-recurse-submodules" in args
    assert "--" in args
    assert captured["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["kwargs"]["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert "shell" not in captured["kwargs"]


class NetworkFailureProcess(FakeProcess):
    async def communicate(self):
        return (
            b"",
            b"fatal: unable to access 'https://github.com/owner/repo.git/': "
            b"Recv failure: Connection was reset",
        )


@pytest.mark.asyncio
async def test_git_retries_once_after_connection_reset(tmp_path: Path, monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return NetworkFailureProcess(returncode=128)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    destination = tmp_path / "clone"
    source = await clone_github("https://github.com/owner/repo", destination)
    assert source.name == "owner/repo"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_git_reports_network_reset_after_retry(tmp_path: Path, monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return NetworkFailureProcess(returncode=128)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(SourceIngestError) as error:
        await clone_github("https://github.com/owner/repo", tmp_path / "clone")
    message = str(error.value)
    assert "网络连接被重置" in message
    assert "ZIP 上传" in message
    assert len(calls) == 2


def test_directory_size_ignores_git_metadata(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "pack.bin").write_bytes(b"x" * 4096)

    assert ingest.directory_size(tmp_path, excluded=frozenset({".git"})) == len("print('hello')")
    assert ingest.directory_size(tmp_path) == len("print('hello')") + 4096


def test_git_checkout_failure_is_reported_as_long_path_problem():
    message, retryable = ingest._clone_failure(
        b"Cloning into 'x'...\n"
        b"error: unable to create file long/path.txt: Filename too long\n"
        b"fatal: unable to checkout working tree\n"
        b"warning: Clone succeeded, but checkout failed.\n"
        b"You can inspect what was checked out with 'git status'\n"
        b"and retry with 'git restore --source=HEAD :/'\n"
    )
    assert "路径过长" in message
    assert retryable is False


def test_remove_tree_deletes_read_only_git_objects(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git" / "objects").mkdir(parents=True)
    (repo / "app.py").write_text("print('hello')", encoding="utf-8")
    pack = repo / ".git" / "objects" / "pack.idx"
    pack.write_bytes(b"x" * 128)
    pack.chmod(stat.S_IREAD)

    ingest.remove_tree(repo)

    assert not repo.exists()
