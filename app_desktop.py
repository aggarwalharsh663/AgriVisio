"""
AgriScan - Standalone Windows Desktop Application Launcher
Uses pywebview with native Windows WebView2 (Chromium Edge engine)
to host the FastAPI backend and interactive frontend in a native window.
"""

import os
import sys
import time
import socket
import threading
import urllib.request
import uvicorn
import webview

def find_free_port(preferred: int = 8000) -> int:
    """Check if preferred port is free; if not, bind to any available ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

def wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll health endpoint until server is ready to accept requests."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False

def start_backend(port: int):
    """Run uvicorn server in a dedicated thread."""
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

def main():
    # Ensure current working directory is the project root
    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)

    port = find_free_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    server_thread = threading.Thread(target=start_backend, args=(port,), daemon=True)
    server_thread.start()

    if not wait_for_server(server_url):
        print(f"Error: AgriScan backend failed to respond at {server_url}", file=sys.stderr)
        sys.exit(1)

    # Launch native Windows Desktop window
    window = webview.create_window(
        title="AgriScan - Precision Optical Grain Assessment",
        url=server_url,
        width=1340,
        height=880,
        min_size=(980, 660),
        text_select=True,
        zoomable=True,
    )

    webview.start(debug=False)

if __name__ == "__main__":
    main()
