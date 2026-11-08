const { app, BrowserWindow, protocol, shell, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const readline = require('readline');
const { spawn, exec } = require('child_process');
const crypto = require('crypto');

// Register custom scheme 'app' before ready - ZERO NETWORK PORTS
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'app',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
      bypassCSP: true,
    },
  },
]);

const LOG_FILE = path.join(__dirname, 'electron.log');
function log(...args) {
  const msg = `[${new Date().toISOString()}] ${args.join(' ')}`;
  try { fs.appendFileSync(LOG_FILE, msg + '\n', 'utf-8'); } catch (e) {}
  console.log(msg);
}

process.on('uncaughtException', (err) => {
  log('UNCAUGHT EXCEPTION:', err && err.stack ? err.stack : err);
});
process.on('unhandledRejection', (reason) => {
  log('UNHANDLED REJECTION:', reason);
});

let mainWindow = null;
let splashWindow = null;
let pythonProcess = null;
let isQuitting = false;

// Directory paths
const ELECTRON_DIR = __dirname;
const ROOT_DIR = path.resolve(ELECTRON_DIR, '..');
const BACKEND_DIR = path.join(ROOT_DIR, 'backend');
const FRONTEND_DIR = path.join(ROOT_DIR, 'frontend');
const DATA_DIR = path.join(ROOT_DIR, 'data');
const TEMP_UPLOADS = path.join(DATA_DIR, 'temp_uploads');

if (!fs.existsSync(TEMP_UPLOADS)) {
  try { fs.mkdirSync(TEMP_UPLOADS, { recursive: true }); } catch (e) {}
}

const candidatePythons = [
  path.join(BACKEND_DIR, 'python', 'python.exe'),
  path.join(ROOT_DIR, 'python', 'python.exe'),
  path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
  path.join(ROOT_DIR, '.venv', 'python.exe'),
  path.join(ROOT_DIR, 'python.exe'),
];
let PYTHON_EXE = candidatePythons.find((p) => fs.existsSync(p)) || 'python';
const WORKER_SCRIPT = path.join(BACKEND_DIR, 'ipc_worker.py');

const ICON_PATH = path.join(ROOT_DIR, 'app_icon.ico');
const LOGO_PATH = path.join(FRONTEND_DIR, 'assets', 'logo.png');

let logoBase64 = '';
if (fs.existsSync(LOGO_PATH)) {
  try {
    logoBase64 = fs.readFileSync(LOGO_PATH).toString('base64');
  } catch (e) {}
}

// --- Portless Python IPC Bridge (stdin / stdout pipes) ---
const pendingRequests = new Map();
let isWorkerReady = false;
let workerReadyResolve = null;
const workerReadyPromise = new Promise((resolve) => {
  workerReadyResolve = resolve;
});

function startPythonWorker() {
  log('Spawning portless Python worker:', PYTHON_EXE, WORKER_SCRIPT);

  pythonProcess = spawn(
    PYTHON_EXE,
    [WORKER_SCRIPT],
    {
      cwd: ROOT_DIR,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONPATH: ROOT_DIR,
        PYTHONUNBUFFERED: '1',
      }),
    }
  );

  const rl = readline.createInterface({
    input: pythonProcess.stdout,
    crlfDelay: Infinity,
  });

  rl.on('line', (line) => {
    line = line.trim();
    if (!line) return;
    try {
      const msg = JSON.parse(line);
      if (msg.event === 'ready') {
        log('Python worker is READY (Portless IPC established).');
        isWorkerReady = true;
        if (workerReadyResolve) workerReadyResolve();
        return;
      }

      const reqId = msg.id;
      if (reqId && pendingRequests.has(reqId)) {
        const { resolve, reject } = pendingRequests.get(reqId);
        pendingRequests.delete(reqId);
        if (msg.ok) {
          resolve(msg.result);
        } else {
          reject(new Error(msg.error || 'Worker request failed.'));
        }
      }
    } catch (e) {
      log('Worker stdout parse error:', e.message, line);
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    log('[Python stderr]', data.toString().trim());
  });

  pythonProcess.on('error', (err) => {
    log('Python process spawn error:', err);
  });

  pythonProcess.on('exit', (code, signal) => {
    log(`Python process exited with code ${code}, signal ${signal}`);
    for (const [id, { reject }] of pendingRequests.entries()) {
      reject(new Error('Python worker terminated unexpectedly.'));
    }
    pendingRequests.clear();
  });
}

function sendIpcRequest(action, data = {}, timeoutMs = 60000) {
  return new Promise(async (resolve, reject) => {
    if (!isWorkerReady) {
      await Promise.race([
        workerReadyPromise,
        new Promise((_, r) => setTimeout(() => r(new Error('Timed out waiting for Python worker to initialize.')), 25000)),
      ]);
    }

    if (!pythonProcess || pythonProcess.killed) {
      return reject(new Error('Python worker is not running.'));
    }

    const reqId = crypto.randomUUID();
    const timer = setTimeout(() => {
      if (pendingRequests.has(reqId)) {
        pendingRequests.delete(reqId);
        reject(new Error(`Request '${action}' timed out after ${timeoutMs}ms.`));
      }
    }, timeoutMs);

    pendingRequests.set(reqId, {
      resolve: (res) => { clearTimeout(timer); resolve(res); },
      reject: (err) => { clearTimeout(timer); reject(err); },
    });

    const payload = JSON.stringify({ id: reqId, action, data }) + '\n';
    pythonProcess.stdin.write(payload, 'utf-8');
  });
}

// --- MIME types for file serving ---
const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.webm': 'video/webm',
  '.mp4': 'video/mp4',
};

// --- Custom app:// Protocol Handler (Zero Network Ports) ---
function registerProtocolHandler() {
  protocol.handle('app', async (req) => {
    try {
      const parsedUrl = new URL(req.url);
      let pathname = decodeURIComponent(parsedUrl.pathname);

      // 1. API: /api/health
      if (pathname === '/api/health') {
        const result = await sendIpcRequest('health', {});
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 2. API: /api/heartbeat
      if (pathname === '/api/heartbeat') {
        const result = await sendIpcRequest('heartbeat', {});
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 3. API: /api/heartbeat/status
      if (pathname === '/api/heartbeat/status') {
        return new Response(JSON.stringify({ active: true, seconds_since_last: 0 }), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 4. API: /api/scan (Simulated optical scan)
      if (pathname === '/api/scan' && req.method === 'POST') {
        const body = await req.json().catch(() => ({}));
        const result = await sendIpcRequest('scan', body);
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 5. API: /api/scan/upload (Multipart photo upload)
      if (pathname === '/api/scan/upload' && req.method === 'POST') {
        const formData = await req.formData();
        const file = formData.get('image') || formData.get('file');
        if (!file) {
          return new Response(JSON.stringify({ detail: 'No photo uploaded.' }), {
            status: 400,
            headers: { 'content-type': 'application/json' },
          });
        }

        const ext = path.extname(file.name || 'uploaded.jpg').toLowerCase() || '.jpg';
        const tempName = `upload-${crypto.randomUUID()}${ext}`;
        const tempPath = path.join(TEMP_UPLOADS, tempName);

        const arrayBuffer = await file.arrayBuffer();
        fs.writeFileSync(tempPath, Buffer.from(arrayBuffer));

        try {
          const result = await sendIpcRequest('scan_upload', {
            file_path: tempPath,
            sample_name: formData.get('sample_name') || '',
            variety: formData.get('variety') || '',
            lighting: formData.get('lighting') || 'ring_light',
          });

          return new Response(JSON.stringify(result), {
            headers: { 'content-type': 'application/json' },
          });
        } finally {
          try {
            if (fs.existsSync(tempPath)) fs.unlinkSync(tempPath);
          } catch (e) {}
        }
      }

      // 6. API: /api/batches
      if (pathname === '/api/batches' && req.method === 'GET') {
        const result = await sendIpcRequest('list_batches', {});
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 7. API: /api/batches/:batch_id
      const batchMatch = pathname.match(/^\/api\/batches\/([^/]+)$/);
      if (batchMatch && req.method === 'GET') {
        const batchId = decodeURIComponent(batchMatch[1]);
        const result = await sendIpcRequest('get_batch', { batch_id: batchId });
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 8. Static uploaded media & batch files (/files/...)
      if (pathname.startsWith('/files/')) {
        const rel = pathname.slice('/files/'.length);
        const filePath = path.join(DATA_DIR, rel);
        if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
          const ext = path.extname(filePath).toLowerCase();
          const contentType = mimeTypes[ext] || 'application/octet-stream';
          const fileData = fs.readFileSync(filePath);
          return new Response(fileData, {
            headers: {
              'content-type': contentType,
              'cache-control': 'public, max-age=31536000, immutable',
            },
          });
        }
        return new Response('File Not Found', { status: 404 });
      }

      // 9. Static frontend assets from FRONTEND_DIR
      if (pathname === '/' || pathname === '' || pathname === '/index.html') {
        const indexPath = path.join(FRONTEND_DIR, 'index.html');
        if (fs.existsSync(indexPath)) {
          const html = fs.readFileSync(indexPath, 'utf-8');
          return new Response(html, {
            headers: {
              'content-type': 'text/html; charset=utf-8',
              'cache-control': 'no-cache, no-store, must-revalidate',
            },
          });
        }
      }

      const filePath = path.join(FRONTEND_DIR, pathname.startsWith('/') ? pathname.slice(1) : pathname);
      if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
        const ext = path.extname(filePath).toLowerCase();
        const contentType = mimeTypes[ext] || 'application/octet-stream';
        const fileData = fs.readFileSync(filePath);
        return new Response(fileData, {
          headers: {
            'content-type': contentType,
            'cache-control': pathname.endsWith('.html') || pathname.endsWith('.js') || pathname.endsWith('.css')
              ? 'no-cache, no-store, must-revalidate'
              : 'public, max-age=86400',
          },
        });
      }

      return new Response('Not Found', { status: 404 });
    } catch (err) {
      log('[Protocol Error]', err);
      return new Response(JSON.stringify({ detail: err.message || 'Internal Error', error: 'scan_failed' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      });
    }
  });
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

  // Zero-port local application loading
  mainWindow.loadURL('app://agriscan/index.html');

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
  setTimeout(showMainWindow, 2500);

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

// Native PDF Export Dialog (Download Report)
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
  if (pythonProcess) {
    try {
      if (process.platform === 'win32' && pythonProcess.pid) {
        exec(`taskkill /pid ${pythonProcess.pid} /T /F`, () => {});
      } else {
        pythonProcess.kill();
      }
    } catch (e) {}
    pythonProcess = null;
  }
}

app.whenReady().then(async () => {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.agriscan.ricequalityanalyser');
  }

  registerProtocolHandler();
  createSplash();
  startPythonWorker();
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
