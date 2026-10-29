"""
AgriScan - Standalone Windows Desktop Application Launcher
Architecture:
- High-performance FastAPI backend in background thread
- Native standalone application window using Edge Chromium App Mode (with pywebview fallback)
- Pure desktop experience: no browser tabs, no address bar, custom icon, instant 1s launch
- Automatic clean shutdown on window close
"""

import os
import sys
import time
import socket
import threading
import subprocess
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

def find_free_port(preferred: int = 8000) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

def start_backend_server(port: int):
    """Run uvicorn server in background thread."""
    import uvicorn
    from backend.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()

def wait_for_server(url: str, timeout: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.08)
    return False

def find_edge_path():
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def main():
    port = find_free_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    # 1. Start FastAPI backend in background daemon thread
    server_thread = threading.Thread(target=start_backend_server, args=(port,), daemon=True)
    server_thread.start()

    # 2. Poll until server is healthy (typically takes < 1 second)
    if not wait_for_server(server_url):
        print(f"Error: AgriScan backend failed to start at {server_url}", file=sys.stderr)
        sys.exit(1)

    # 3. Launch standalone native desktop window
    edge_exe = find_edge_path()
    if edge_exe:
        profile_dir = os.path.join(PROJECT_ROOT, ".desktop_profile")
        cmd = [
            edge_exe,
            f"--app={server_url}",
            f"--user-data-dir={profile_dir}",
            "--window-size=1360,900",
            "--disable-features=Translate",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        proc = subprocess.Popen(cmd)
        proc.wait()
    else:
        # Fallback to pywebview if Edge is unavailable
        try:
            import webview
            icon_path = os.path.join(PROJECT_ROOT, "app_icon.ico")
            webview.create_window(
                "AgriScan - Precision Optical Grain Assessment",
                url=server_url,
                width=1340,
                height=880,
                min_size=(980, 660),
            )
            webview.start(debug=False, icon=icon_path)
        except Exception:
            import webbrowser
            webbrowser.open(server_url)

if __name__ == "__main__":
    main()
