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
    },
  },
]);

const CSP_POLICY = "default-src 'self' app:; script-src 'self' app:; style-src 'self' 'unsafe-inline' app: https://fonts.googleapis.com; font-src 'self' app: https://fonts.gstatic.com data:; img-src 'self' app: data: blob:; connect-src 'self' app: http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self' app:; frame-ancestors 'none';";

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
let lastWorkerHeartbeat = Date.now();
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
      lastWorkerHeartbeat = Date.now();
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
      try {
        await Promise.race([
          workerReadyPromise,
          new Promise((_, r) => setTimeout(() => r(new Error('Timed out waiting for Python worker to initialize.')), 25000)),
        ]);
      } catch (err) {
        return reject(err);
      }
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

const UPLOAD_MAX_BYTES = 10 * 1024 * 1024; // 10 MB

function safeDecodeURI(uri) {
  if (typeof uri !== 'string') return null;
  if (uri.includes('\0') || uri.includes('%00')) return null;
  let decoded = uri;
  let prev = '';
  let iterations = 0;
  while (decoded !== prev && iterations < 3) {
    prev = decoded;
    try {
      decoded = decodeURIComponent(decoded);
    } catch (e) {
      return null;
    }
    iterations++;
  }
  if (decoded.includes('\0')) return null;
  return decoded;
}

function resolveContainedPath(baseDir, requestedPath) {
  if (!requestedPath || typeof requestedPath !== 'string') return null;
  const decoded = safeDecodeURI(requestedPath);
  if (decoded === null) return null;

  // Reject traversal sequences in raw or decoded form
  if (
    decoded.includes('..') ||
    decoded.includes('\\') ||
    requestedPath.includes('..') ||
    requestedPath.includes('%2e') ||
    requestedPath.includes('%2E') ||
    requestedPath.includes('%5c') ||
    requestedPath.includes('%5C')
  ) {
    return null;
  }

  // Normalize and strip leading slashes
  const normalizedRel = path.normalize(decoded).replace(/^[\/\\]+/, '');
  if (normalizedRel.startsWith('..') || path.isAbsolute(normalizedRel)) {
    return null;
  }

  const resolvedBase = path.resolve(baseDir);
  const resolvedTarget = path.resolve(resolvedBase, normalizedRel);

  // Containment check via path.relative
  const rel = path.relative(resolvedBase, resolvedTarget);
  if (rel.startsWith('..') || path.isAbsolute(rel)) {
    return null;
  }

  // Strict boundary check (protect against sibling prefix collisions)
  if (resolvedTarget !== resolvedBase && !resolvedTarget.startsWith(resolvedBase + path.sep)) {
    return null;
  }

  // Symlink escape check
  if (fs.existsSync(resolvedTarget)) {
    try {
      const realTarget = fs.realpathSync(resolvedTarget);
      const realBase = fs.realpathSync(resolvedBase);
      const realRel = path.relative(realBase, realTarget);
      if (realRel.startsWith('..') || path.isAbsolute(realRel)) {
        return null;
      }
      if (realTarget !== realBase && !realTarget.startsWith(realBase + path.sep)) {
        return null;
      }
      return realTarget;
    } catch (e) {
      return null;
    }
  }

  return resolvedTarget;
}

function isValidImageBuffer(buf) {
  if (!buf || buf.length < 12) return false;
  // JPEG: FF D8 FF
  if (buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return true;
  // PNG: 89 50 4E 47 0D 0A 1A 0A
  if (buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) return true;
  // WebP: RIFF .... WEBP
  if (
    buf[0] === 0x52 && buf[1] === 0x49 && buf[2] === 0x46 && buf[3] === 0x46 &&
    buf[8] === 0x57 && buf[9] === 0x45 && buf[10] === 0x42 && buf[11] === 0x50
  ) return true;
  return false;
}

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
        lastWorkerHeartbeat = Date.now();
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 3. API: /api/heartbeat/status
      if (pathname === '/api/heartbeat/status') {
        const isAlive = Boolean(pythonProcess && !pythonProcess.killed && isWorkerReady);
        const secondsSince = (Date.now() - lastWorkerHeartbeat) / 1000.0;
        return new Response(JSON.stringify({
          active: isAlive && secondsSince < 30,
          seconds_since_last: Math.round(secondsSince * 100) / 100,
        }), {
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

      // 4b. API: /api/scan/hardware (Epson scanner capture)
      if (pathname === '/api/scan/hardware' && req.method === 'POST') {
        const body = await req.json().catch(() => ({}));
        try {
          const result = await sendIpcRequest('scan_hardware', body, 120000);
          return new Response(JSON.stringify(result), {
            headers: { 'content-type': 'application/json' },
          });
        } catch (err) {
          const msg = err.message || '';
          if (msg.includes('No scanner') || msg.includes('scanner not found') || msg.includes('no scanner')) {
            return new Response(JSON.stringify({
              error: 'scanner_not_found',
              detail: 'No scanner detected. Connect a scanner or upload a photo instead.',
            }), {
              status: 404,
              headers: { 'content-type': 'application/json' },
            });
          }
          return new Response(JSON.stringify({
            error: 'scan_failed',
            detail: msg || 'Scanner capture failed.',
          }), {
            status: 500,
            headers: { 'content-type': 'application/json' },
          });
        }
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

        const arrayBuffer = await file.arrayBuffer();
        if (arrayBuffer.byteLength > UPLOAD_MAX_BYTES) {
          return new Response(JSON.stringify({ detail: 'Photo must be 10 MB or smaller.' }), {
            status: 413,
            headers: { 'content-type': 'application/json' },
          });
        }

        const buffer = Buffer.from(arrayBuffer);
        if (!isValidImageBuffer(buffer)) {
          return new Response(JSON.stringify({ detail: 'Choose a valid JPEG, PNG, or WebP photo.' }), {
            status: 422,
            headers: { 'content-type': 'application/json' },
          });
        }

        const ext = path.extname(file.name || 'uploaded.jpg').toLowerCase() || '.jpg';
        const tempName = `upload-${crypto.randomUUID()}${ext}`;
        const tempPath = path.join(TEMP_UPLOADS, tempName);

        fs.writeFileSync(tempPath, buffer);

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

      // 8. Static uploaded media & batch files (/files/...) - hardened with extension whitelist and batch scoping
      if (pathname.startsWith('/files/')) {
        const rawRel = pathname.slice('/files/'.length);
        const normRel = rawRel.replace(/^[\/\\]+/, '');
        // Require batch directory scoping (batches/<batch_id>/... or AS-.../...)
        if (!normRel.startsWith('batches/') && !/^AS-[\w-]+\//.test(normRel)) {
          return new Response('Access Denied: Unscoped batch file access', { status: 403 });
        }
        const ext = path.extname(normRel).toLowerCase();
        const allowedExts = ['.jpg', '.jpeg', '.png', '.webp', '.json'];
        const blockedExts = [
          '.sqlite3', '.db', '.auth_token', '.py', '.pyc', '.joblib',
          '.env', '.key', '.pem', '.log', '.bat', '.cmd', '.ps1', '.sh',
          '.webm', '.mp4', '.exe', '.dll', '.ini'
        ];
        if (!allowedExts.includes(ext) || blockedExts.includes(ext)) {
          return new Response('Access Denied: Forbidden file type', { status: 403 });
        }
        const resolvedPath = resolveContainedPath(DATA_DIR, rawRel);
        if (!resolvedPath) {
          return new Response('Access Denied', { status: 403 });
        }
        if (fs.existsSync(resolvedPath) && fs.statSync(resolvedPath).isFile()) {
          const contentType = mimeTypes[ext] || 'application/octet-stream';
          const fileData = fs.readFileSync(resolvedPath);
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
              'x-content-type-options': 'nosniff',
              'x-frame-options': 'DENY',
              'content-security-policy': CSP_POLICY,
            },
          });
        }
      }

      const rawStaticRel = pathname.replace(/^\/+/, '');
      const resolvedStatic = resolveContainedPath(FRONTEND_DIR, rawStaticRel);
      if (!resolvedStatic) {
        return new Response('Access Denied', { status: 403 });
      }
      if (fs.existsSync(resolvedStatic) && fs.statSync(resolvedStatic).isFile()) {
        const ext = path.extname(resolvedStatic).toLowerCase();
        const contentType = mimeTypes[ext] || 'application/octet-stream';
        const fileData = fs.readFileSync(resolvedStatic);
        return new Response(fileData, {
          headers: {
            'content-type': contentType,
            'cache-control': resolvedStatic.endsWith('.html') || resolvedStatic.endsWith('.js') || resolvedStatic.endsWith('.css')
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
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      navigateOnDragDrop: false,
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
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, navigationUrl) => {
    try {
      const parsedUrl = new URL(navigationUrl);
      if (parsedUrl.origin !== 'app://agriscan') {
        event.preventDefault();
        if (navigationUrl.startsWith('http://') || navigationUrl.startsWith('https://')) {
          shell.openExternal(navigationUrl);
        }
      }
    } catch (e) {
      event.preventDefault();
    }
  });

  mainWindow.webContents.on('will-attach-webview', (event) => {
    event.preventDefault();
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
    const safeBatchId = String(batchId || 'summary').replace(/[^a-zA-Z0-9_-]/g, '_');
    const defaultFilename = `AgriScan-Report-${safeBatchId}.pdf`;
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

function stopBackend(callback) {
  if (!pythonProcess) {
    if (callback) callback();
    return;
  }
  const proc = pythonProcess;
  pythonProcess = null;

  try {
    log('Initiating graceful shutdown of Python worker...');
    if (proc.stdin && !proc.stdin.destroyed) {
      try {
        proc.stdin.write(JSON.stringify({ action: 'shutdown' }) + '\n');
        proc.stdin.end();
      } catch (e) {}
    }

    let exited = false;
    const forceKillTimer = setTimeout(() => {
      if (!exited) {
        log('Graceful shutdown timeout (3000ms) expired; terminating process forcibly.');
        try {
          if (process.platform === 'win32' && proc.pid) {
            spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { windowsHide: true });
          } else {
            proc.kill('SIGKILL');
          }
        } catch (e) {}
      }
      if (callback) callback();
    }, 3000);

    proc.once('exit', (code, signal) => {
      exited = true;
      clearTimeout(forceKillTimer);
      log(`Python worker exited cleanly (code: ${code}, signal: ${signal}).`);
      if (callback) callback();
    });
  } catch (err) {
    log('Error during stopBackend:', err);
    if (callback) callback();
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
