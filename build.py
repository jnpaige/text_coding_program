#!/usr/bin/env python3
"""
build.py — Launcher entry point and PyInstaller build script.

Two modes:
  1. As the packaged entry point (what the .exe runs):
       Starts the FastAPI server and opens the browser automatically.

  2. As the build script (run from the dev environment):
       uv run python build.py --build
       Invokes PyInstaller to produce dist/text_coding_program/

The dist/ folder is self-contained — zip it and distribute.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def find_open_port(host: str = "127.0.0.1", preferred: int = 8090) -> int:
    for offset in range(20):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return preferred


def launch():
    """Start the server and open the browser. This is what the .exe runs."""
    # Ensure projects/ dir exists next to the executable (or script)
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent

    os.chdir(str(base))

    host = "127.0.0.1"
    port = find_open_port(host)
    url = f"http://{host}:{port}"

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"Starting Text Coding Program at {url}")
    print("Close this window to stop the server.\n")

    import uvicorn
    from server import app, _projects_dir
    _projects_dir.mkdir(exist_ok=True)

    uvicorn.run(app, host=host, port=port)


def build():
    """Invoke PyInstaller to create the distributable bundle."""
    spec_file = Path(__file__).parent / "text_coding_program.spec"

    if not spec_file.exists():
        # Generate the spec file
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--name", "text_coding_program",
            "--noconfirm",
            "--console",
            "--add-data", f"static{os.pathsep}static",
            "--add-data", f"server.py{os.pathsep}.",
            "--hidden-import", "uvicorn.logging",
            "--hidden-import", "uvicorn.loops",
            "--hidden-import", "uvicorn.loops.auto",
            "--hidden-import", "uvicorn.protocols",
            "--hidden-import", "uvicorn.protocols.http",
            "--hidden-import", "uvicorn.protocols.http.auto",
            "--hidden-import", "uvicorn.protocols.websockets",
            "--hidden-import", "uvicorn.protocols.websockets.auto",
            "--hidden-import", "uvicorn.lifespan",
            "--hidden-import", "uvicorn.lifespan.on",
            "--collect-submodules", "pymupdf",
            "build.py",
        ]
        print("Running PyInstaller...")
        subprocess.run(cmd, check=True)
    else:
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec_file)]
        print(f"Building from {spec_file}...")
        subprocess.run(cmd, check=True)

    dist_dir = Path("dist/text_coding_program")
    # Ensure projects/ dir exists in the dist
    (dist_dir / "projects").mkdir(exist_ok=True)

    print(f"\nBuild complete: {dist_dir}")
    print(f"To distribute: zip the '{dist_dir}' folder and share it.")
    print(f"Users double-click text_coding_program.exe to start.")


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        launch()
