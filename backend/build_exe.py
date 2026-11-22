from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("构建前端需要 Node.js 和 npm。")
    subprocess.run(
        [npm, "--prefix", str(ROOT / "frontend"), "run", "build"],
        cwd=ROOT,
        check=True,
    )
    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "CodeScope",
        "--paths",
        str(ROOT),
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(build_dir / "pyinstaller"),
        "--specpath",
        str(build_dir),
        "--add-data",
        f"{ROOT / 'frontend' / 'dist'}{os.pathsep}frontend/dist",
        "--collect-submodules",
        "uvicorn",
        "--collect-all",
        "webview",
        "--hidden-import",
        "webview.platforms.edgechromium",
        "--hidden-import",
        "webview.platforms.winforms",
        "--hidden-import",
        "sqlalchemy.dialects.sqlite",
        "--hidden-import",
        "multipart",
        str(ROOT / "backend" / "desktop.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"已生成：{ROOT / 'dist' / 'CodeScope.exe'}")


if __name__ == "__main__":
    main()
