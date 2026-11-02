const { app, BrowserWindow, shell, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn, exec } = require('child_process');

let mainWindow = null;
let splashWindow = null;
let backendProcess = null;
let isQuitting = false;

const BACKEND_PORT = 8000;
const APP_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// Directory paths
const ELECTRON_DIR = __dirname;
const ROOT_DIR = path.resolve(ELECTRON_DIR, '..');
const BACKEND_DIR = path.join(ROOT_DIR, 'backend');
const FRONTEND_DIR = path.join(ROOT_DIR, 'frontend');

const candidatePythons = [
  path.join(BACKEND_DIR, 'python', 'python.exe'),
  path.join(ROOT_DIR, 'python', 'python.exe'),
  path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
  path.join(ROOT_DIR, '.venv', 'python.exe'),
  path.join(ROOT_DIR, 'python.exe'),
];
let PYTHON_EXE = candidatePythons.find((p) => fs.existsSync(p)) || 'python';

const ICON_PATH = path.join(ROOT_DIR, 'app_icon.ico');
const LOGO_PATH = path.join(FRONTEND_DIR, 'assets', 'logo.png');

let logoBase64 = '';
if (fs.existsSync(LOGO_PATH)) {
  try {
    logoBase64 = fs.readFileSync(LOGO_PATH).toString('base64');
  } catch (e) {}
}

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 480,
    height: 320,
    frame: false,
    resizable: false,
    center: true,
    show: true,
    alwaysOnTop: true,
    backgroundColor: '#0d0f14',
    icon: ICON_PATH,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  const logoSrc = logoBase64 ? `data:image/png;base64,${logoBase64}` : '';

  const splashHtml = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; user-select: none; }
  body {
    background: #0d0f14;
    color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    border: 2px solid #2d354d;
    border-radius: 14px;
    overflow: hidden;
    position: relative;
    padding: 24px;
    background-image: radial-gradient(circle at 50% 45%, rgba(245, 158, 11, 0.16) 0%, transparent 65%);
  }
  .logo-box {
    margin-bottom: 20px;
    display: flex;
    justify-content: center;
  }
  .logo-img {
    max-width: 280px;
    height: auto;
    display: block;
    filter: drop-shadow(0 8px 24px rgba(0,0,0,0.6));
  }
  .progress-wrap {
    width: 280px;
    height: 6px;
    background: #171b26;
    border-radius: 6px;
    overflow: hidden;
    position: relative;
    border: 1px solid #2d354d;
    margin-bottom: 14px;
  }
  .progress-bar {
    position: absolute;
    top: 0; left: 0; bottom: 0;
    width: 40%;
    background: #f59e0b;
    border-radius: 6px;
    animation: slide 1.4s ease-in-out infinite;
  }
  @keyframes slide {
    0% { left: -40%; width: 25%; }
    50% { width: 50%; }
    100% { left: 100%; width: 25%; }
  }
  .status {
    font-size: 11px;
    color: #94a3b8;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
</style>
</head>
<body>
  <div class="logo-box">
    ${logoSrc ? `<img src="${logoSrc}" class="logo-img" alt="AgriScan">` : '<h1 style="color:#f59e0b;">AGRI SCAN</h1>'}
  </div>
  <div class="progress-wrap">
    <div class="progress-bar"></div>
  </div>
  <div class="status">Starting local optical AI engine &amp; classifier...</div>
</body>
</html>`;

  splashWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(splashHtml));
}

function checkServerReady(url, timeoutMs = 25000, intervalMs = 200) {
  const startTime = Date.now();
  return new Promise((resolve) => {
    let done = false;
    const timer = setInterval(() => {
      if (Date.now() - startTime > timeoutMs) {
        clearInterval(timer);
        if (!done) {
          done = true;
          resolve(false);
        }
        return;
      }
      const req = http.get(url, (res) => {
        if (!done && res.statusCode < 500) {
          done = true;
          clearInterval(timer);
          resolve(true);
        }
      });
      req.on('error', () => {});
      req.setTimeout(600, () => req.destroy());
    }, intervalMs);
  });
}

function startBackend() {
  return new Promise((resolve) => {
    let launched = false;
    const launch = () => {
      if (launched) return;
      launched = true;
      console.log('[AgriScan Desktop] Starting background FastAPI backend on port 8000...');
      try {
        const logPath = path.join(ROOT_DIR, 'backend.log');
        let stdioOption = 'ignore';
        try {
          const logFd = fs.openSync(logPath, 'a');
          stdioOption = ['ignore', logFd, logFd];
        } catch (e) {
          stdioOption = 'ignore';
        }

        backendProcess = spawn(
          PYTHON_EXE,
          ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
          {
            cwd: ROOT_DIR,
            windowsHide: true,
            shell: false,
            stdio: stdioOption,
            env: Object.assign({}, process.env, { PYTHONPATH: ROOT_DIR }),
          }
        );

        backendProcess.on('error', (err) => {
          console.error('[AgriScan Desktop] Failed to start backend:', err);
        });
      } catch (err) {
        console.error('[AgriScan Desktop] Spawn error for backend:', err);
      }

      checkServerReady(APP_URL + '/api/health', 25000).then(() => {
        console.log('[AgriScan Desktop] Backend is ready.');
        resolve();
      });
    };

    const probe = http.get(APP_URL + '/api/health', (res) => {
      if (res.statusCode === 200) {
        console.log('[AgriScan Desktop] Backend already active on port 8000.');
        return resolve();
      }
      launch();
    });

    probe.setTimeout(1000, () => probe.destroy());
    probe.on('error', () => launch());
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1080,
    minHeight: 720,
    title: 'AgriScan - Precision Optical Grain Assessment',
    backgroundColor: '#0d0f14',
    icon: ICON_PATH,
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      spellcheck: false,
      preload: path.join(ELECTRON_DIR, 'preload.cjs'),
    },
  });

  mainWindow.loadURL(APP_URL);

  const showMainWindow = () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
    }
  };

  mainWindow.once('ready-to-show', showMainWindow);
  setTimeout(showMainWindow, 3500);

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.warn(`[AgriScan Desktop] Page failed to load (${errorCode}: ${errorDescription}), retrying in 1s...`);
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL(APP_URL);
      }
    }, 1000);
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

ipcMain.handle('save-report-pdf', async (event, { batchId }) => {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return { success: false, error: 'Main window unavailable' };
  }

  try {
    const defaultFilename = `AgriScan-Report-${batchId || 'summary'}.pdf`;
    const { filePath, canceled } = await dialog.showSaveDialog(mainWindow, {
      title: 'Save AgriScan Assessment Report',
      defaultPath: path.join(app.getPath('downloads'), defaultFilename),
      filters: [
        { name: 'PDF Documents (*.pdf)', extensions: ['pdf'] },
        { name: 'All Files (*.*)', extensions: ['*'] },
      ],
    });

    if (canceled || !filePath) {
      return { canceled: true };
    }

    const pdfBuffer = await mainWindow.webContents.printToPDF({
      printBackground: true,
      pageSize: 'A4',
      margins: {
        top: 0.4,
        bottom: 0.4,
        left: 0.4,
        right: 0.4,
      },
    });

    fs.writeFileSync(filePath, pdfBuffer);

    try {
      shell.showItemInFolder(filePath);
    } catch (e) {}

    return { success: true, filePath, canceled: false };
  } catch (err) {
    console.error('[AgriScan Desktop] Failed to save report PDF:', err);
    return { success: false, error: err.message };
  }
});

function stopBackend() {
  if (backendProcess) {
    try {
      if (process.platform === 'win32' && backendProcess.pid) {
        exec(`taskkill /pid ${backendProcess.pid} /T /F`, () => {});
      } else {
        backendProcess.kill();
      }
    } catch (e) {}
    backendProcess = null;
  }
}

app.whenReady().then(async () => {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.agriscan.ricequalityanalyser');
  }

  createSplash();
  await startBackend();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  isQuitting = true;
  stopBackend();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  stopBackend();
});
