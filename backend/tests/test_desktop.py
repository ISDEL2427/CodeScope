from __future__ import annotations

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend import desktop


@pytest.fixture
def native_environment(monkeypatch: pytest.MonkeyPatch, tmp_path):
    events: list[str] = []
    errors: list[str] = []

    class FakeConfig:
        def __init__(self, app, **kwargs):
            self.app = app
            self.kwargs = kwargs

    class FakeServer:
        instances = []

        def __init__(self, config):
            self.config = config
            self.should_exit = False
            self.__class__.instances.append(self)

        def run(self):
            pass

    class FakeThread:
        instances = []

        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon
            self.alive = False
            self.join_timeout = None
            self.__class__.instances.append(self)

        def start(self):
            self.alive = True
            events.append("server-started")

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.join_timeout = timeout
            self.alive = False
            events.append("server-joined")

    uvicorn = ModuleType("uvicorn")
    uvicorn.Config = FakeConfig
    uvicorn.Server = FakeServer
    main = ModuleType("backend.app.main")
    main.app = object()
    webview = ModuleType("webview")
    webview.windows = []
    webview.start_calls = []

    def create_window(title, **kwargs):
        events.append("window-created")
        webview.windows.append((title, kwargs))

    def start(**kwargs):
        events.append("window-started")
        webview.start_calls.append(kwargs)

    webview.create_window = create_window
    webview.start = start

    monkeypatch.setattr(desktop, "application_url", lambda: "http://127.0.0.1:8000")
    monkeypatch.setattr(desktop, "prepare_runtime", lambda: tmp_path)
    monkeypatch.setattr(desktop, "port_is_busy", lambda url: False)
    monkeypatch.setattr(desktop, "show_error", errors.append)
    monkeypatch.setattr(desktop.threading, "Thread", FakeThread)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setitem(sys.modules, "backend.app.main", main)
    monkeypatch.setitem(sys.modules, "webview", webview)

    return SimpleNamespace(
        events=events,
        errors=errors,
        server_class=FakeServer,
        thread_class=FakeThread,
        webview=webview,
    )


def test_owned_server_opens_native_window_after_health_and_stops_on_close(
    monkeypatch: pytest.MonkeyPatch,
    native_environment,
):
    env = native_environment
    monkeypatch.setattr(desktop, "is_healthy", lambda url: False)

    def healthy(url, thread):
        env.events.append("server-healthy")
        return True

    monkeypatch.setattr(desktop, "wait_for_server", healthy)
    desktop.run()

    assert env.events == [
        "server-started",
        "server-healthy",
        "window-created",
        "window-started",
        "server-joined",
    ]
    assert env.webview.windows[0][1]["url"] == "http://127.0.0.1:8000"
    assert env.webview.start_calls == [{"gui": "edgechromium", "debug": False}]
    assert env.server_class.instances[0].should_exit is True
    assert env.thread_class.instances[0].join_timeout == 8
    assert not env.errors


def test_existing_server_is_reused_without_being_stopped(
    monkeypatch: pytest.MonkeyPatch,
    native_environment,
):
    env = native_environment
    monkeypatch.setattr(desktop, "is_healthy", lambda url: True)
    desktop.run()

    assert env.events == ["window-created", "window-started"]
    assert not env.server_class.instances
    assert not env.thread_class.instances
    assert not env.errors


def test_busy_foreign_port_shows_error_without_starting_anything(
    monkeypatch: pytest.MonkeyPatch,
    native_environment,
):
    env = native_environment
    monkeypatch.setattr(desktop, "is_healthy", lambda url: False)
    monkeypatch.setattr(desktop, "port_is_busy", lambda url: True)
    desktop.run()

    assert "8000" in env.errors[0]
    assert not env.events
    assert not env.server_class.instances


def test_failed_health_wait_cleans_owned_server_without_opening_window(
    monkeypatch: pytest.MonkeyPatch,
    native_environment,
):
    env = native_environment
    monkeypatch.setattr(desktop, "is_healthy", lambda url: False)
    monkeypatch.setattr(desktop, "wait_for_server", lambda url, thread: False)
    desktop.run()

    assert env.events == ["server-started", "server-joined"]
    assert env.server_class.instances[0].should_exit is True
    assert "本地服务启动失败" in env.errors[0]
    assert not env.webview.windows


def test_webview_failure_cleans_owned_server(
    monkeypatch: pytest.MonkeyPatch,
    native_environment,
):
    env = native_environment
    monkeypatch.setattr(desktop, "is_healthy", lambda url: False)
    monkeypatch.setattr(desktop, "wait_for_server", lambda url, thread: True)

    def fail_start(**kwargs):
        raise RuntimeError("WebView2 unavailable")

    env.webview.start = fail_start
    desktop.run()

    assert env.events == ["server-started", "window-created", "server-joined"]
    assert env.server_class.instances[0].should_exit is True
    assert "WebView2 unavailable" in env.errors[0]


def test_desktop_entry_has_no_browser_or_tkinter_launcher():
    source = inspect.getsource(desktop)
    assert "webbrowser" not in source
    assert "tkinter" not in source
    assert "CODE_REVIEW_NO_BROWSER" not in source


class _FakeReportResponse:
    headers = {"Content-Disposition": 'attachment; filename="report.md"'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return "# 报告".encode("utf-8")


def test_save_report_uses_native_dialog(monkeypatch: pytest.MonkeyPatch, tmp_path):
    calls = {}

    class FakeWindow:
        def create_file_dialog(self, dialog_type, save_filename, file_types):
            calls["dialog_type"] = dialog_type
            calls["save_filename"] = save_filename
            calls["file_types"] = file_types
            return (str(tmp_path / "saved.md"),)

    webview = ModuleType("webview")
    webview.windows = [FakeWindow()]
    webview.FileDialog = SimpleNamespace(SAVE=30)
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(
        desktop.urllib.request,
        "urlopen",
        lambda url, timeout: _FakeReportResponse(),
    )

    result = desktop.DesktopApi("http://127.0.0.1:8000").save_report("review-1", "markdown")

    assert result["status"] == "saved"
    assert calls["dialog_type"] == 30
    assert calls["save_filename"] == "report.md"
    assert (tmp_path / "saved.md").read_text(encoding="utf-8") == "# 报告"


def test_save_report_cancel_keeps_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    class FakeWindow:
        def create_file_dialog(self, dialog_type, save_filename, file_types):
            return None

    webview = ModuleType("webview")
    webview.windows = [FakeWindow()]
    webview.FileDialog = SimpleNamespace(SAVE=30)
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(
        desktop.urllib.request,
        "urlopen",
        lambda url, timeout: _FakeReportResponse(),
    )

    result = desktop.DesktopApi("http://127.0.0.1:8000").save_report("review-1", "json")

    assert result == {"status": "cancelled"}
    assert not (tmp_path / "saved.md").exists()


def test_save_report_rejects_invalid_id(monkeypatch: pytest.MonkeyPatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("不应请求报告接口")

    monkeypatch.setattr(desktop.urllib.request, "urlopen", unexpected)

    result = desktop.DesktopApi("http://127.0.0.1:8000").save_report("../escape", "json")

    assert result["status"] == "error"
