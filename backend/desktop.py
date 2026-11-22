from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"


def _download_filename(disposition: str, fallback: str) -> str:
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
    if encoded:
        try:
            return urllib.parse.unquote(encoded.group(1))
        except ValueError:
            pass
    plain = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    return plain.group(1) if plain else fallback


class DesktopApi:
    """暴露给前端的原生能力；WebView2 不处理浏览器式下载，因此改用系统保存对话框。"""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def save_report(self, review_id: str, report_format: str = "json") -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", review_id or ""):
            return {"status": "error", "message": "审查任务标识无效。"}
        selected_format = "markdown" if report_format == "markdown" else "json"
        try:
            url = (
                f"{self.base_url}/api/reviews/{urllib.parse.quote(review_id)}"
                f"/report?format={selected_format}"
            )
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = response.read()
                disposition = response.headers.get("Content-Disposition", "")
        except Exception as exc:
            return {"status": "error", "message": f"无法获取审查报告：{exc}"}

        extension = "md" if selected_format == "markdown" else "json"
        filename = _download_filename(
            disposition, f"code-review-{review_id}.{extension}"
        )
        import webview

        windows = getattr(webview, "windows", None) or []
        if not windows:
            return {"status": "error", "message": "桌面窗口不可用。"}
        dialog_type = getattr(
            getattr(webview, "FileDialog", None),
            "SAVE",
            getattr(webview, "SAVE_DIALOG", 30),
        )
        file_types = (
            ("Markdown 文件 (*.md)",)
            if selected_format == "markdown"
            else ("JSON 文件 (*.json)",)
        )
        selection = windows[0].create_file_dialog(
            dialog_type,
            save_filename=filename,
            file_types=file_types,
        )
        if not selection:
            return {"status": "cancelled"}
        target = Path(selection if isinstance(selection, str) else selection[0])
        try:
            target.write_bytes(payload)
        except OSError as exc:
            return {"status": "error", "message": f"写入报告失败：{exc}"}
        return {"status": "saved", "path": str(target)}


def application_url() -> str:
    port = int(os.getenv("CODE_REVIEW_PORT", "8000"))
    return f"http://{HOST}:{port}"


def is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=0.4) as response:
            return json.load(response).get("status") == "ok"
    except (OSError, ValueError):
        return False


def port_is_busy(url: str) -> bool:
    port = int(url.rsplit(":", 1)[1])
    with socket.socket() as client:
        client.settimeout(0.3)
        return client.connect_ex((HOST, port)) == 0


def prepare_runtime() -> Path:
    configured = os.getenv("CODE_REVIEW_RUNTIME_DIR")
    if configured:
        runtime = Path(configured)
    else:
        local_app_data = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        runtime = local_app_data / "CodeScope"
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ["CODE_REVIEW_RUNTIME_DIR"] = str(runtime)
    return runtime


def show_error(message: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, "CodeScope", 0x10)


def wait_for_server(url: str, server_thread: threading.Thread, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while server_thread.is_alive() and time.monotonic() < deadline:
        if is_healthy(url):
            return True
        time.sleep(0.15)
    return False


def run() -> None:
    url = application_url()
    runtime: Path | None = None
    server = None
    server_thread: threading.Thread | None = None

    try:
        if not is_healthy(url):
            if port_is_busy(url):
                show_error(f"端口 {url.rsplit(':', 1)[1]} 已被其他程序占用。")
                return

            runtime = prepare_runtime()
            if sys.stdout is None:
                sys.stdout = (runtime / "desktop.out.log").open("a", encoding="utf-8")
            if sys.stderr is None:
                sys.stderr = (runtime / "desktop.error.log").open("a", encoding="utf-8")

            import uvicorn
            from backend.app.main import app

            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=HOST,
                    port=int(url.rsplit(":", 1)[1]),
                    access_log=False,
                    log_level="warning",
                )
            )
            server_thread = threading.Thread(target=server.run, daemon=True)
            server_thread.start()
            if not wait_for_server(url, server_thread):
                show_error(f"本地服务启动失败。日志目录：{runtime}")
                return

        import webview

        webview.create_window(
            "CodeScope",
            url=url,
            width=1280,
            height=840,
            min_size=(960, 640),
            js_api=DesktopApi(url),
        )
        webview.start(gui="edgechromium", debug=False)
    except Exception as exc:
        details = f"\n日志目录：{runtime}" if runtime else ""
        show_error(f"CodeScope 桌面窗口启动失败。\n{exc}{details}")
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None and server_thread.is_alive():
            server_thread.join(timeout=8)


if __name__ == "__main__":
    run()
