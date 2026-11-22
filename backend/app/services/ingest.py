from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO
from urllib.parse import urlsplit, urlunsplit

from fastapi import UploadFile


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
GITHUB_MAX_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
_SIZE_EXCLUDED_DIRECTORIES = frozenset({".git"})
_NETWORK_FAILURE_MARKERS = (
    "connection was reset",
    "connection reset",
    "recv failure",
    "rpc failed",
    "early eof",
    "unexpected disconnect",
    "tls handshake",
    "ssl error",
    "could not resolve host",
    "failed to connect",
    "network is unreachable",
    "operation timed out",
    "connection timed out",
)
_NOT_FOUND_MARKERS = ("repository not found", "not found", "404")
_AUTH_MARKERS = (
    "authentication failed",
    "could not read username",
    "terminal prompts disabled",
    "permission denied",
)
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SourceIngestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubSource:
    url: str
    name: str


def validate_github_url(repo_url: str) -> GitHubSource:
    try:
        parsed = urlsplit(repo_url.strip())
        _ = parsed.port
    except ValueError as exc:
        raise SourceIngestError("GitHub 仓库地址格式无效。") from exc
    if parsed.scheme.lower() != "https":
        raise SourceIngestError("仅支持 HTTPS GitHub 公共仓库地址。")
    if (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        raise SourceIngestError("仅支持 github.com 上的公共仓库。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port:
        raise SourceIngestError("仓库地址不能包含凭据、端口、查询参数或片段。")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise SourceIngestError("请输入标准仓库地址，例如 https://github.com/owner/repo。")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    if (
        not _OWNER_RE.fullmatch(owner)
        or not _REPO_RE.fullmatch(repository)
        or repository in {".", ".."}
        or repository.startswith("-")
    ):
        raise SourceIngestError("GitHub 仓库所有者或仓库名无效。")
    canonical = urlunsplit(("https", "github.com", f"/{owner}/{repository}.git", "", ""))
    return GitHubSource(url=canonical, name=f"{owner}/{repository}")


async def save_upload(upload: UploadFile, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise SourceIngestError("ZIP 上传文件超过 50 MiB 限制。")
                output.write(chunk)
        await upload.close()
        return size
    except Exception:
        destination.unlink(missing_ok=True)
        await upload.close()
        raise


def _zip_target(destination: Path, info: zipfile.ZipInfo) -> Path | None:
    name = info.filename
    if not name or "\x00" in name:
        raise SourceIngestError("ZIP 包含空路径或 NUL 字符。")
    normalized_name = name.replace("\\", "/")
    windows_path = PureWindowsPath(name)
    path = PurePosixPath(normalized_name)
    if (
        normalized_name.startswith("/")
        or windows_path.is_absolute()
        or windows_path.drive
        or re.match(r"^[A-Za-z]:", normalized_name)
    ):
        raise SourceIngestError(f"ZIP 条目使用绝对路径或盘符：{name[:120]}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SourceIngestError(f"ZIP 条目包含不安全路径：{name[:120]}")
    if any(":" in part for part in path.parts):
        raise SourceIngestError(f"ZIP 条目包含 Windows 保留字符：{name[:120]}")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise SourceIngestError(f"ZIP 不允许符号链接：{name[:120]}")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise SourceIngestError(f"ZIP 包含非常规文件：{name[:120]}")
    target = destination.joinpath(*path.parts)
    resolved_destination = destination.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_destination and resolved_destination not in resolved_target.parents:
        raise SourceIngestError("ZIP 条目试图逃离解压目录。")
    if info.is_dir() or name.endswith(("/", "\\")):
        return None
    return target


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    extracted_total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise SourceIngestError("ZIP 条目数超过 10000 限制。")
            for info in entries:
                target = _zip_target(destination, info)
                if target is None:
                    continue
                if info.flag_bits & 0x1:
                    raise SourceIngestError("不支持加密 ZIP 文件。")
                if info.file_size > MAX_FILE_BYTES:
                    raise SourceIngestError(f"ZIP 单文件超过 5 MiB：{info.filename[:120]}")
                extracted_total += info.file_size
                if extracted_total > MAX_EXTRACTED_BYTES:
                    raise SourceIngestError("ZIP 解压总大小超过 200 MiB 限制。")
                if info.file_size > 1024:
                    ratio = info.file_size / max(info.compress_size, 1)
                    if info.compress_size == 0 or ratio > MAX_COMPRESSION_RATIO:
                        raise SourceIngestError(f"ZIP 文件压缩比异常：{info.filename[:120]}")
                relative = target.relative_to(destination).as_posix()
                identity = relative.casefold()
                if identity in seen:
                    raise SourceIngestError(f"ZIP 包含重复路径：{info.filename[:120]}")
                seen.add(identity)
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                try:
                    with bundle.open(info, "r") as source, target.open("xb") as output:
                        while chunk := source.read(1024 * 1024):
                            written += len(chunk)
                            if written > info.file_size or written > MAX_FILE_BYTES:
                                raise SourceIngestError("ZIP 解压大小与清单不一致。")
                            output.write(chunk)
                except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
                    raise SourceIngestError("ZIP 条目无法安全读取或已损坏。") from exc
        if not seen:
            raise SourceIngestError("ZIP 中没有可审查文件。")
    except SourceIngestError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise SourceIngestError("ZIP 文件损坏、格式无效或无法写入解压目录。") from exc


def directory_size(root: Path, *, excluded: frozenset[str] = frozenset()) -> int:
    total = 0
    if not root.exists():
        return total
    for base, dirnames, files in os.walk(root, followlinks=False):
        if excluded:
            dirnames[:] = [name for name in dirnames if name not in excluded]
        for name in files:
            try:
                path = Path(base) / name
                if not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def remove_tree(root: Path) -> None:
    """删除目录树；Git 对象文件是只读的，需先清除只读属性才能完整清理。"""
    if not root.exists():
        return
    for base, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            try:
                os.chmod(Path(base) / name, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(root, ignore_errors=True)


def _sanitize_git_output(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace")
    text = re.sub(r"//[^/@\s]*@", "//", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(("fatal:", "error:")):
            break
    else:
        line = lines[-1]
    # 克隆进度会用 \r 刷新同一行，取最后一段以避免混入进度条。
    return line.split("\r")[-1][:240]


def _clone_failure(stderr: bytes) -> tuple[str, bool]:
    """返回面向用户的失败说明，以及该失败是否值得自动重试。"""
    detail = _sanitize_git_output(stderr)
    lowered = detail.lower()
    if "filename too long" in lowered or "unable to checkout working tree" in lowered:
        return (
            "克隆完成但检出失败，通常是 Windows 路径过长导致。"
            "请将数据目录设置到更短的路径后重试。",
            False,
        )
    if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
        return "GitHub 仓库不存在或不可公开访问，请检查仓库地址。", False
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return "该仓库需要登录才能访问，当前仅支持公共仓库。", False
    if any(marker in lowered for marker in _NETWORK_FAILURE_MARKERS):
        return (
            "无法连接 GitHub，网络连接被重置或中断。"
            "请检查网络、代理或 VPN 后重试，也可以改用 ZIP 上传。",
            True,
        )
    fallback = "GitHub 克隆失败，请确认地址正确且仓库可公开访问。"
    return (f"{fallback}（{detail}）" if detail else fallback), False


class _CloneNetworkError(RuntimeError):
    pass


async def _clone_once(
    url: str,
    destination: Path,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "GCM_INTERACTIVE": "never",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_NO_LAZY_FETCH": "1",
        }
    )
    command = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "submodule.recurse=false",
        "-c",
        "core.longpaths=true",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
        "clone",
        "--depth",
        "1",
        "--no-recurse-submodules",
        "--single-branch",
        "--",
        url,
        str(destination),
    ]
    process = None
    succeeded = False
    communicate_task: asyncio.Task[tuple[bytes, bytes]] | None = None
    monitor_task: asyncio.Task[None] | None = None
    limit_mib = max_bytes // (1024 * 1024)

    async def monitor_size() -> None:
        while communicate_task is not None and not communicate_task.done():
            size = await asyncio.to_thread(directory_size, destination, excluded=_SIZE_EXCLUDED_DIRECTORIES)
            if size > max_bytes:
                raise SourceIngestError(f"GitHub 仓库源码超过 {limit_mib} MiB 大小限制。")
            await asyncio.sleep(0.2)

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            cwd=str(destination.parent),
        )
        communicate_task = asyncio.create_task(process.communicate())
        monitor_task = asyncio.create_task(monitor_size())
        done, _ = await asyncio.wait(
            {communicate_task, monitor_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate_task not in done:
            if monitor_task in done:
                monitor_task.result()
            raise TimeoutError
        _, stderr = await communicate_task
        if process.returncode != 0:
            message, retryable = _clone_failure(stderr or b"")
            if retryable:
                raise _CloneNetworkError(message)
            raise SourceIngestError(message)
        size = await asyncio.to_thread(directory_size, destination, excluded=_SIZE_EXCLUDED_DIRECTORIES)
        if size > max_bytes:
            raise SourceIngestError(f"GitHub 仓库源码超过 {limit_mib} MiB 大小限制。")
        succeeded = True
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
        if communicate_task is not None and not communicate_task.done():
            communicate_task.cancel()
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
        tasks = [task for task in (communicate_task, monitor_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except (TimeoutError, OSError):
                pass
        if not succeeded:
            await asyncio.to_thread(remove_tree, destination)


async def clone_github(
    repo_url: str,
    destination: Path,
    *,
    timeout_seconds: float = 120,
    max_bytes: int = GITHUB_MAX_BYTES,
) -> GitHubSource:
    source = validate_github_url(repo_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        for attempt in range(2):
            try:
                await _clone_once(
                    source.url,
                    destination,
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )
                return source
            except _CloneNetworkError as exc:
                if attempt == 1:
                    raise SourceIngestError(str(exc)) from exc
    except TimeoutError as exc:
        raise SourceIngestError("GitHub 克隆超时，请稍后重试或改用 ZIP 上传。") from exc
    except FileNotFoundError as exc:
        raise SourceIngestError("未找到 Git，请先安装 Git 或改用 ZIP 上传。") from exc
    except SourceIngestError:
        raise
    except OSError as exc:
        raise SourceIngestError("无法启动 Git 克隆，请检查本地 Git 环境。") from exc
    raise SourceIngestError("GitHub 克隆失败，请确认地址正确且仓库可公开访问。")
