"""
AgriScan - Standalone Windows Desktop Application Launcher
Architecture:
- Instant native Tkinter splash screen (<50ms startup time)
- Background Uvicorn daemon with automatic port selection
- Automatic transition to native WebView2 desktop window upon health-check confirmation (~1.2s)
- Custom Windows taskbar and titlebar icon via webview.start(icon=...) + AppUserModelID
"""

import os
import sys
import time
import socket
import ctypes
import threading
import urllib.request

# Ensure Windows assigns this process its own dedicated taskbar identity (prevents python grouping)
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("agriscan.precision.optics.1.0")
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

ICON_PATH = os.path.join(PROJECT_ROOT, "app_icon.ico")
if not os.path.exists(ICON_PATH):
    ICON_PATH = os.path.join(PROJECT_ROOT, "agriscan.ico")

LOGO_PATH = os.path.join(PROJECT_ROOT, "frontend", "assets", "logo.png")

def find_free_port(preferred: int = 8000) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

def start_backend_server(port: int):
    """Run uvicorn server in a dedicated thread."""
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

def show_splash_until_ready(server_url: str) -> bool:
    """Shows native lightweight splash screen while server starts in background."""
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
    except Exception:
        # Fallback if tkinter or PIL fails: poll directly
        start = time.time()
        while time.time() - start < 20.0:
            try:
                with urllib.request.urlopen(f"{server_url}/api/health", timeout=0.5) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                time.sleep(0.15)
        return False

    root = tk.Tk()
    root.title("AgriScan")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#0d0f14")

    # Dimensions & screen centering
    width, height = 480, 320
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    # Set icon for splash window if possible
    if os.path.exists(ICON_PATH):
        try:
            root.iconbitmap(ICON_PATH)
        except Exception:
            pass

    # Neo-Industrial styled card frame
    frame = tk.Frame(root, bg="#0d0f14", highlightbackground="#2d354d", highlightcolor="#f59e0b", highlightthickness=2)
    frame.pack(fill="both", expand=True)

    # Logo image
    photo = None
    if os.path.exists(LOGO_PATH):
        try:
            logo_img = Image.open(LOGO_PATH)
            logo_img.thumbnail((340, 160), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(logo_img)
            logo_label = tk.Label(frame, image=photo, bg="#0d0f14")
            logo_label.image = photo  # Keep reference
            logo_label.pack(pady=(45, 12))
        except Exception:
            fallback_label = tk.Label(frame, text="AGRI SCAN", font=("Segoe UI", 24, "bold"), fg="#f59e0b", bg="#0d0f14")
            fallback_label.pack(pady=(60, 15))
    else:
        fallback_label = tk.Label(frame, text="AGRI SCAN", font=("Segoe UI", 24, "bold"), fg="#f59e0b", bg="#0d0f14")
        fallback_label.pack(pady=(60, 15))

    # Animated progress bar
    progress_canvas = tk.Canvas(frame, width=280, height=6, bg="#1a1e2b", highlightthickness=1, highlightbackground="#2d354d")
    progress_canvas.pack(pady=12)
    bar = progress_canvas.create_rectangle(0, 0, 50, 6, fill="#f59e0b", width=0)

    status_label = tk.Label(
        frame,
        text="INITIALIZING OPTICAL & ML CORE...",
        font=("Segoe UI", 9, "bold"),
        fg="#94a3b8",
        bg="#0d0f14",
    )
    status_label.pack(pady=(4, 20))

    start_time = time.time()
    bar_pos = [0]
    is_ready = [False]

    def poll():
        # Update progress animation
        bar_pos[0] = (bar_pos[0] + 14) % 280
        progress_canvas.coords(bar, bar_pos[0], 0, min(bar_pos[0] + 75, 280), 6)

        try:
            with urllib.request.urlopen(f"{server_url}/api/health", timeout=0.25) as resp:
                if resp.status == 200:
                    status_label.config(text="READY - LAUNCHING...", fg="#10b981")
                    progress_canvas.coords(bar, 0, 0, 280, 6)
                    root.update()
                    time.sleep(0.18)
                    is_ready[0] = True
                    root.destroy()
                    return
        except Exception:
            pass

        elapsed = time.time() - start_time
        if elapsed > 20.0:
            root.destroy()
            return

        if elapsed > 2.0:
            status_label.config(text="WARMING UP CLASSIFIER & CAMERA...", fg="#f59e0b")

        root.after(65, poll)

    root.after(40, poll)
    root.mainloop()
    return is_ready[0]

def main():
    port = find_free_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    # 1. Start FastAPI backend IMMEDIATELY in background daemon thread
    server_thread = threading.Thread(target=start_backend_server, args=(port,), daemon=True)
    server_thread.start()

    # 2. Show native instant splash screen while backend warms up
    ready = show_splash_until_ready(server_url)
    if not ready:
        print(f"Error: AgriScan backend failed to start at {server_url}", file=sys.stderr)
        sys.exit(1)

    # 3. Create native webview window directly pointing to running server
    import webview
    webview.create_window(
        title="AgriScan - Precision Optical Grain Assessment",
        url=server_url,
        width=1340,
        height=880,
        min_size=(980, 660),
        text_select=True,
        zoomable=True,
    )

    # 4. Start GUI event loop passing custom icon directly to native window
    webview.start(debug=False, icon=ICON_PATH)

if __name__ == "__main__":
    main()
