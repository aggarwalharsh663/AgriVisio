"""
AgriScan - Standalone Windows Desktop Application Launcher
Features:
- Instant launch splash screen with animated progress and brand logo
- Custom Windows taskbar and titlebar icon (SetCurrentProcessExplicitAppUserModelID + WM_SETICON)
- Dedicated background FastAPI daemon with automatic port fallback
- Smooth transition from splash to inspection console upon backend health check
"""

import os
import sys
import time
import socket
import base64
import ctypes
import threading
import urllib.request

# Ensure Windows treats this process as its own app (prevents python.exe taskbar grouping & icon)
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("agriscan.precision.optics.1.0")
    except Exception:
        pass

import webview

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

ICON_PATH = os.path.join(PROJECT_ROOT, "agriscan.ico")
LOGO_PATH = os.path.join(PROJECT_ROOT, "frontend", "assets", "logo.png")

def get_logo_data_uri() -> str:
    if os.path.exists(LOGO_PATH):
        try:
            with open(LOGO_PATH, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
                return f"data:image/png;base64,{encoded}"
        except Exception:
            pass
    return ""

def generate_splash_html() -> str:
    logo_uri = get_logo_data_uri()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgriScan Loading</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0d0f14;
    color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
  }}
  .atmosphere {{
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: radial-gradient(circle at 50% 45%, rgba(245, 158, 11, 0.16) 0%, transparent 65%);
  }}
  .splash-container {{
    position: relative;
    z-index: 10;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    padding: 40px;
    max-width: 520px;
    width: 90%;
  }}
  .logo-box {{
    margin-bottom: 30px;
    animation: popIn 0.5s cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .logo-img {{
    max-width: 340px;
    height: auto;
    display: block;
    filter: drop-shadow(0 12px 28px rgba(0,0,0,0.7));
  }}
  .progress-shell {{
    width: 320px;
    height: 8px;
    background: #171b26;
    border: 2px solid #2d354d;
    border-radius: 999px;
    overflow: hidden;
    margin-bottom: 18px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  }}
  .progress-bar {{
    height: 100%;
    width: 25%;
    background: #f59e0b;
    border-radius: 999px;
    animation: pulseTravel 1.8s infinite ease-in-out;
  }}
  .status-line {{
    font-family: "JetBrains Mono", ui-monospace, "SF Mono", monospace;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #94a3b8;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .status-dot {{
    width: 7px;
    height: 7px;
    background: #f59e0b;
    border-radius: 50%;
    animation: blink 1s infinite alternate;
  }}
  @keyframes popIn {{
    0% {{ opacity: 0; transform: scale(0.92) translateY(8px); }}
    100% {{ opacity: 1; transform: scale(1) translateY(0); }}
  }}
  @keyframes pulseTravel {{
    0% {{ width: 15%; margin-left: 0; }}
    50% {{ width: 55%; margin-left: 35%; }}
    100% {{ width: 25%; margin-left: 75%; }}
  }}
  @keyframes blink {{
    from {{ opacity: 0.3; }}
    to {{ opacity: 1; }}
  }}
</style>
</head>
<body>
  <div class="atmosphere"></div>
  <div class="splash-container">
    <div class="logo-box">
      <img src="{logo_uri}" class="logo-img" alt="AgriScan Logo">
    </div>
    <div class="progress-shell">
      <div class="progress-bar"></div>
    </div>
    <div class="status-line">
      <span class="status-dot"></span>
      <span>INITIALIZING OPTICAL & ML ENGINE...</span>
    </div>
  </div>
</body>
</html>"""

def find_free_port(preferred: int = 8000) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

def wait_for_server(url: str, timeout: float = 25.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.12)
    return False

def start_backend_server(port: int):
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

def apply_native_window_icon(window):
    """Set the Win32 / WinForms window and taskbar icon."""
    if sys.platform != "win32" or not os.path.exists(ICON_PATH):
        return

    # 1. Try WinForms Form.Icon
    try:
        import clr
        clr.AddReference("System.Drawing")
        from System.Drawing import Icon
        if hasattr(window, "native") and window.native:
            window.native.Icon = Icon(ICON_PATH)
    except Exception:
        pass

    # 2. Try Win32 SendMessageW (WM_SETICON)
    try:
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040

        hicon = ctypes.windll.user32.LoadImageW(
            None, ICON_PATH, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        if hicon and hasattr(window, "native") and hasattr(window.native, "Handle"):
            hwnd = int(window.native.Handle)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
    except Exception:
        pass

def launch_server_and_load(window, port: int):
    """Starts backend in background and loads URL into window when healthy."""
    server_url = f"http://127.0.0.1:{port}"

    # Start FastAPI Uvicorn backend in background thread
    server_thread = threading.Thread(target=start_backend_server, args=(port,), daemon=True)
    server_thread.start()

    # Poll until server health endpoint responds
    if wait_for_server(server_url, timeout=25.0):
        # Brief pause to ensure full CSS/canvas readiness
        time.sleep(0.35)
        window.load_url(server_url)
    else:
        print(f"Error: AgriScan backend failed to respond at {server_url}", file=sys.stderr)

def main():
    port = find_free_port(8000)

    # 1. Create window immediately with instant splash screen
    window = webview.create_window(
        title="AgriScan - Precision Optical Grain Assessment",
        html=generate_splash_html(),
        width=1340,
        height=880,
        min_size=(980, 660),
        text_select=True,
        zoomable=True,
    )

    def on_shown():
        apply_native_window_icon(window)
        # Launch backend and load app into window when ready
        bg_worker = threading.Thread(target=launch_server_and_load, args=(window, port), daemon=True)
        bg_worker.start()

    window.events.shown += on_shown

    # 2. Start webview GUI loop
    webview.start(debug=False)

if __name__ == "__main__":
    main()
