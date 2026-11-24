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

function cleanupStaleTempUploads() {
  try {
    if (!fs.existsSync(TEMP_UPLOADS)) return;
    const entries = fs.readdirSync(TEMP_UPLOADS);
    const now = Date.now();
    const MAX_AGE_MS = 15 * 60 * 1000; // 15 minutes max age for temporary upload files
    for (const entry of entries) {
      const fullPath = path.join(TEMP_UPLOADS, entry);
      try {
        const stats = fs.statSync(fullPath);
        if (stats.isFile() && (now - stats.mtimeMs > MAX_AGE_MS)) {
          fs.unlinkSync(fullPath);
          log(`[Cleanup] Removed stale temp upload file: ${entry}`);
        }
      } catch (e) {}
    }
  } catch (e) {}
}

cleanupStaleTempUploads();
setInterval(cleanupStaleTempUploads, 15 * 60 * 1000).unref();

const candidatePythons = [
  path.join(BACKEND_DIR, 'python', 'python.exe'),
  path.join(ROOT_DIR, 'python', 'python.exe'),
  path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
  path.join(ROOT_DIR, '.venv', 'python.exe'),
  path.join(ROOT_DIR, 'python.exe'),
  ...(process.env.LOCALAPPDATA
    ? [path.join(process.env.LOCALAPPDATA, 'Programs', 'AgriVisio', 'backend', 'python', 'python.exe')]
    : []),
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
let workerReadyReject = null;
let lastWorkerHeartbeat = Date.now();
let workerReadyPromise = new Promise((resolve, reject) => {
  workerReadyResolve = resolve;
  workerReadyReject = reject;
});

function startPythonWorker() {
  if (pythonProcess && !pythonProcess.killed) {
    log('Python worker process is already running (PID:', pythonProcess.pid, '); skipping duplicate spawn.');
    return;
  }

  log('Spawning portless Python worker with parent watchdog PID:', process.pid, PYTHON_EXE, WORKER_SCRIPT);

  const proc = spawn(
    PYTHON_EXE,
    [WORKER_SCRIPT, '--parent-pid', String(process.pid)],
    {
      cwd: ROOT_DIR,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONPATH: ROOT_DIR,
        PYTHONUNBUFFERED: '1',
        AGRIVISIO_PARENT_PID: String(process.pid),
        AGRIVISIO_IPC_MODE: '1',
      }),
    }
  );
  pythonProcess = proc;

  const rl = readline.createInterface({
    input: proc.stdout,
    crlfDelay: Infinity,
  });

  rl.on('line', (line) => {
    // Guard against stale process output after respawn
    if (pythonProcess !== proc) return;
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
      } else if (!reqId && !msg.ok) {
        // Log uncorresponded line error without destroying unrelated in-flight requests (CR-02)
        log('Worker reported stream-level IPC error without ID:', msg.error);
      }
    } catch (e) {
      log('Worker stdout parse error:', e.message);
    }
  });

  proc.stderr.on('data', (data) => {
    log('[Python stderr]', data.toString().trim());
  });

  proc.on('error', (err) => {
    log('Python process spawn error:', err);
    // Guard against stale process events after respawn
    if (pythonProcess !== proc) return;
    if (workerReadyReject && !isWorkerReady) {
      workerReadyReject(new Error('Python worker spawn error: ' + err.message));
    }
    pythonProcess = null;
    isWorkerReady = false;
    workerReadyPromise = new Promise((resolve, reject) => {
      workerReadyResolve = resolve;
      workerReadyReject = reject;
    });
  });

  proc.on('exit', (code, signal) => {
    log(`Python process exited with code ${code}, signal ${signal}`);
    // Guard against stale process events after respawn
    if (pythonProcess !== proc) return;
    if (workerReadyReject && !isWorkerReady) {
      workerReadyReject(new Error(`Python worker exited before becoming ready (code ${code}, signal ${signal}).`));
    }
    pythonProcess = null;
    isWorkerReady = false;
    workerReadyPromise = new Promise((resolve, reject) => {
      workerReadyResolve = resolve;
      workerReadyReject = reject;
    });
    for (const [id, { reject }] of pendingRequests.entries()) {
      reject(new Error('Python worker terminated unexpectedly.'));
    }
    pendingRequests.clear();
  });
}

function sendIpcRequest(action, data = {}, timeoutMs = 60000) {
  return new Promise(async (resolve, reject) => {
    if ((!pythonProcess || pythonProcess.killed) && !isQuitting) {
      if (action === 'shutdown') {
        return resolve({ status: 'already_stopped' });
      }
      log('Python worker is not running; attempting auto-respawn...');
      try {
        startPythonWorker();
      } catch (spawnErr) {
        return reject(new Error('Failed to auto-respawn Python worker: ' + spawnErr.message));
      }
    }

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

let currentSessionToken = null;
let authGeneration = 0;

async function performElectronBootstrap() {
  const secret = crypto.randomBytes(32).toString('hex');
  const genAtStart = authGeneration;
  try {
    log('Registering startup bootstrap secret via portless IPC...');
    await sendIpcRequest('register_bootstrap', { secret });
    log('Performing bootstrap exchange via portless IPC...');
    const result = await sendIpcRequest('bootstrap', { secret });
    if (result && result.session_token) {
      if (authGeneration !== genAtStart) {
        log('Session generation changed during bootstrap (e.g. user logged out); discarding stale token.');
        return false;
      }
      currentSessionToken = result.session_token;
      log('Bootstrap authenticated successfully. Local session established.');
      return true;
    }
  } catch (err) {
    log('Bootstrap authentication failed:', err.message);
  }
  return false;
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

      // 1b. API: /api/auth/bootstrap (S-02)
      if (pathname === '/api/auth/bootstrap' && req.method === 'POST') {
        const body = await req.json().catch(() => ({}));
        const secret = (body.bootstrap_secret || '').trim();
        try {
          const result = await sendIpcRequest('bootstrap', { secret });
          if (result && result.session_token) {
            currentSessionToken = result.session_token;
            return new Response(JSON.stringify({ status: 'authenticated' }), {
              headers: {
                'content-type': 'application/json',
                'set-cookie': `agrivisio_session=${result.session_token}; HttpOnly; SameSite=Lax; Path=/`,
              },
            });
          }
        } catch (err) {
          return new Response(JSON.stringify({ detail: 'Invalid, expired, or previously consumed bootstrap secret.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
      }

      // 1c. API: /api/auth/logout (S-02)
      if (pathname === '/api/auth/logout' && req.method === 'POST') {
        currentSessionToken = null;
        authGeneration++;
        return new Response(JSON.stringify({ status: 'logged_out' }), {
          headers: {
            'content-type': 'application/json',
            'set-cookie': 'agrivisio_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax',
          },
        });
      }

      // 1d. API: /api/auth/status (T-01)
      if (pathname === '/api/auth/status') {
        return new Response(JSON.stringify({ authenticated: Boolean(currentSessionToken) }), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 1e. API: /api/auth/reauthenticate (Desktop session recovery)
      if (pathname === '/api/auth/reauthenticate' && req.method === 'POST') {
        const genAtStart = authGeneration;
        const success = await performElectronBootstrap();
        if (success && currentSessionToken && authGeneration === genAtStart) {
          return new Response(JSON.stringify({ status: 'authenticated' }), {
            headers: {
              'content-type': 'application/json',
              'set-cookie': `agrivisio_session=${currentSessionToken}; HttpOnly; SameSite=Lax; Path=/`,
            },
          });
        }
        return new Response(JSON.stringify({ detail: 'Desktop session re-authentication failed.' }), {
          status: 401,
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
        if (!currentSessionToken) {
          return new Response(JSON.stringify({ detail: 'Unauthorized: Desktop session expired or unauthenticated.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
        const body = await req.json().catch(() => ({}));
        const result = await sendIpcRequest('scan', body);
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 4b. API: /api/scan/hardware (Epson scanner capture)
      if (pathname === '/api/scan/hardware' && req.method === 'POST') {
        if (!currentSessionToken) {
          return new Response(JSON.stringify({ detail: 'Unauthorized: Desktop session expired or unauthenticated.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
        const body = await req.json().catch(() => ({}));
        try {
          const result = await sendIpcRequest('scan_hardware', body, 120000);
          return new Response(JSON.stringify(result), {
            headers: { 'content-type': 'application/json' },
          });
        } catch (err) {
          const msg = err.message || '';
          if (msg.includes('unsupported') || msg.includes('Unsupported')) {
            return new Response(JSON.stringify({
              error: 'hardware_scan_unsupported',
              detail: 'Direct hardware scanning is not supported on this platform or environment.',
            }), {
              status: 501,
              headers: { 'content-type': 'application/json' },
            });
          }
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
        if (!currentSessionToken) {
          return new Response(JSON.stringify({ detail: 'Unauthorized: Desktop session expired or unauthenticated.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
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

        function parseOptionalCalibrationFloat(value, paramName) {
          if (value === null || value === undefined) return null;
          const str = String(value).trim();
          if (str === '' || str.toLowerCase() === 'null' || str.toLowerCase() === 'none' || str.toLowerCase() === 'undefined') {
            return null;
          }
          const num = Number(str);
          if (!Number.isFinite(num)) {
            throw new Error(`${paramName} must be a valid numeric value.`);
          }
          if (num <= 0) {
            throw new Error(`${paramName} must be greater than zero.`);
          }
          return num;
        }

        let calibMmPerPixel = null;
        let refLengthMm = null;
        let refPixels = null;

        try {
          calibMmPerPixel = parseOptionalCalibrationFloat(formData.get('calibration_mm_per_pixel'), 'calibration_mm_per_pixel');
          refLengthMm = parseOptionalCalibrationFloat(formData.get('reference_length_mm'), 'reference_length_mm');
          refPixels = parseOptionalCalibrationFloat(formData.get('reference_pixels'), 'reference_pixels');

          if ((refLengthMm !== null && refPixels === null) || (refLengthMm === null && refPixels !== null)) {
            throw new Error('Both reference_length_mm and reference_pixels must be provided together for physical reference calibration.');
          }
        } catch (valErr) {
          return new Response(JSON.stringify({ detail: valErr.message, error: 'validation_error' }), {
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
            calibration_mm_per_pixel: calibMmPerPixel,
            reference_length_mm: refLengthMm,
            reference_pixels: refPixels,
          });

          return new Response(JSON.stringify(result), {
            headers: { 'content-type': 'application/json' },
          });
        } catch (ipcErr) {
          const errMsg = ipcErr.message || 'The uploaded photo could not be analyzed.';
          const isValidation = errMsg.includes('must be') || errMsg.includes('required') || errMsg.includes('greater than zero') || errMsg.includes('valid');
          return new Response(JSON.stringify({ detail: errMsg, error: isValidation ? 'validation_error' : 'scan_failed' }), {
            status: isValidation ? 422 : 500,
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
        if (!currentSessionToken) {
          return new Response(JSON.stringify({ detail: 'Unauthorized: Desktop session expired or unauthenticated.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
        const pageParam = parsedUrl.searchParams.get('page');
        const pageSizeParam = parsedUrl.searchParams.get('page_size');

        let page = null;
        if (pageParam !== null) {
          const parsed = Number(pageParam);
          if (!Number.isInteger(parsed) || parsed < 1) {
            return new Response(JSON.stringify({ detail: 'Invalid page parameter: must be an integer >= 1' }), {
              status: 422,
              headers: { 'content-type': 'application/json' },
            });
          }
          page = parsed;
        }

        let pageSize = null;
        if (pageSizeParam !== null) {
          const parsed = Number(pageSizeParam);
          if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
            return new Response(JSON.stringify({ detail: 'Invalid page_size parameter: must be between 1 and 100' }), {
              status: 422,
              headers: { 'content-type': 'application/json' },
            });
          }
          pageSize = parsed;
        }

        const result = await sendIpcRequest('list_batches', {
          page,
          page_size: pageSize,
        });
        return new Response(JSON.stringify(result), {
          headers: { 'content-type': 'application/json' },
        });
      }

      // 7. API: /api/batches/:batch_id
      const batchMatch = pathname.match(/^\/api\/batches\/([^/]+)$/);
      if (batchMatch && req.method === 'GET') {
        if (!currentSessionToken) {
          return new Response(JSON.stringify({ detail: 'Unauthorized: Desktop session expired or unauthenticated.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
        const batchId = decodeURIComponent(batchMatch[1]);
        try {
          const result = await sendIpcRequest('get_batch', { batch_id: batchId });
          if (!result) {
            return new Response(JSON.stringify({ detail: 'Batch not found' }), {
              status: 404,
              headers: { 'content-type': 'application/json' },
            });
          }
          return new Response(JSON.stringify(result), {
            headers: { 'content-type': 'application/json' },
          });
        } catch (err) {
          const errMsg = err && err.message ? err.message : String(err);
          if (/not found/i.test(errMsg) || /404/.test(errMsg)) {
            return new Response(JSON.stringify({ detail: 'Batch not found' }), {
              status: 404,
              headers: { 'content-type': 'application/json' },
            });
          }
          log('[get_batch IPC Error]', errMsg);
          return new Response(JSON.stringify({ detail: errMsg, error: 'ipc_error' }), {
            status: 500,
            headers: { 'content-type': 'application/json' },
          });
        }
      }

      // 7b. API: /api/batches/:batch_id/export (JSON report export)
      const exportMatch = pathname.match(/^\/api\/batches\/([^/]+)\/export$/);
      if (exportMatch && req.method === 'GET') {
        if (!currentSessionToken) {
          return new Response(JSON.stringify({ detail: 'Unauthorized: Desktop session expired or unauthenticated.' }), {
            status: 401,
            headers: { 'content-type': 'application/json' },
          });
        }
        const batchId = decodeURIComponent(exportMatch[1]);
        try {
          const result = await sendIpcRequest('get_batch', { batch_id: batchId });
          if (!result) {
            return new Response(JSON.stringify({ detail: 'Batch not found' }), {
              status: 404,
              headers: { 'content-type': 'application/json' },
            });
          }
          if (result.is_mock) {
            return new Response(JSON.stringify({ detail: 'Mock batches cannot be exported as genuine grading certificates or measurement reports.' }), {
              status: 400,
              headers: { 'content-type': 'application/json' },
            });
          }
          const safeBatchId = batchId.replace(/[^a-zA-Z0-9_-]/g, '');
          return new Response(JSON.stringify(result, null, 2), {
            headers: {
              'content-type': 'application/json',
              'content-disposition': `attachment; filename="AgriVisio-Report-${safeBatchId}.json"`,
            },
          });
        } catch (err) {
          const errMsg = err && err.message ? err.message : String(err);
          if (/not found/i.test(errMsg) || /404/.test(errMsg)) {
            return new Response(JSON.stringify({ detail: 'Batch not found' }), {
              status: 404,
              headers: { 'content-type': 'application/json' },
            });
          }
          log('[export IPC Error]', errMsg);
          return new Response(JSON.stringify({ detail: errMsg, error: 'ipc_error' }), {
            status: 500,
            headers: { 'content-type': 'application/json' },
          });
        }
      }

      // 8. Static uploaded media & batch files (/files/...) - hardened with auth, extension whitelist and batch scoping
      if (pathname.startsWith('/files/')) {
        if (!currentSessionToken) {
          return new Response('Unauthorized', { status: 401 });
        }
        const rawRel = pathname.slice('/files/'.length);
        const normRel = rawRel.replace(/^[\/\\]+/, '');
        // Require batch directory scoping (batches/<batch_id>/... or AV-.../... / AS-.../...)
        if (!normRel.startsWith('batches/') && !/^(AV|AS)-[\w-]+\//.test(normRel)) {
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
              'cache-control': 'private, max-age=86400',
              'x-content-type-options': 'nosniff',
              'x-frame-options': 'DENY',
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

      const errorPage = path.join(FRONTEND_DIR, 'error.html');
      if (fs.existsSync(errorPage)) {
        const errorHtml = fs.readFileSync(errorPage);
        return new Response(errorHtml, {
          status: 404,
          headers: {
            'content-type': 'text/html; charset=utf-8',
            'cache-control': 'no-cache, no-store, must-revalidate',
            'x-content-type-options': 'nosniff',
            'content-security-policy': CSP_POLICY,
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
    width: 440,
    height: 330,
    frame: false,
    resizable: false,
    center: true,
    show: true,
    alwaysOnTop: true,
    backgroundColor: '#0b0f17',
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
    background: #0b0f17;
    color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    border: 1px solid #1e293b;
    border-radius: 16px;
    overflow: hidden;
    position: relative;
    padding: 24px;
    background-image: radial-gradient(circle at 50% 36%, rgba(34, 197, 94, 0.14) 0%, transparent 68%);
  }
  .logo-box {
    margin-bottom: 20px;
    display: flex;
    justify-content: center;
    align-items: center;
  }
  .logo-img {
    width: 140px;
    height: 140px;
    object-fit: contain;
    display: block;
    filter: drop-shadow(0 10px 22px rgba(0,0,0,0.55));
  }
  .progress-wrap {
    width: 260px;
    height: 5px;
    background: #111827;
    border-radius: 6px;
    overflow: hidden;
    position: relative;
    border: 1px solid #1f2937;
    margin-bottom: 14px;
  }
  .progress-bar {
    position: absolute;
    top: 0; left: 0; bottom: 0;
    width: 38%;
    background: linear-gradient(90deg, #10b981, #22c55e, #84cc16);
    border-radius: 6px;
    animation: slide 1.4s ease-in-out infinite;
  }
  @keyframes slide {
    0% { left: -40%; width: 25%; }
    50% { width: 50%; }
    100% { left: 100%; width: 25%; }
  }
  .status {
    font-size: 10.5px;
    color: #94a3b8;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    text-align: center;
  }
</style>
</head>
<body>
  <div class="logo-box">
    ${logoSrc ? `<img src="${logoSrc}" class="logo-img" alt="AgriVisio">` : '<h1 style="color:#22c55e;">AGRI VISIO</h1>'}
  </div>
  <div class="progress-wrap">
    <div class="progress-bar"></div>
  </div>
  <div class="status">Starting local optical AI engine &amp; classifier...</div>
</body>
</html>`;

  splashWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(splashHtml));
}

let isAuthReady = false;
let isMainWindowReady = false;
let showMainWindow = () => {};

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1080,
    minHeight: 720,
    title: 'AgriVisio - Precision Optical Grain Assessment',
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
  mainWindow.loadURL('app://agrivisio/index.html');

  let mainWindowShown = false;
  showMainWindow = () => {
    if (mainWindowShown) return;
    if (!isAuthReady) {
      log('showMainWindow deferred: optical engine bootstrap still in progress.');
      return;
    }
    mainWindowShown = true;
    log('showMainWindow invoked. splashWindow exists:', !!splashWindow, 'mainWindow exists:', !!mainWindow);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
      log('mainWindow.show() called successfully.');
    }
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
      log('splashWindow.close() called.');
    }
  };

  mainWindow.once('ready-to-show', () => {
    log('mainWindow ready-to-show event fired.');
    isMainWindowReady = true;
    if (isAuthReady) {
      showMainWindow();
    }
  });
  mainWindow.webContents.on('did-fail-load', (e, code, desc, url) => {
    log(`mainWindow webContents did-fail-load: code ${code}, desc "${desc}", url: ${url}`);
  });
  mainWindow.webContents.on('render-process-gone', (e, details) => {
    log('mainWindow render-process-gone:', JSON.stringify(details));
  });
  setTimeout(() => {
    log('Fallback timeout 2500ms reached; invoking showMainWindow if ready.');
    isMainWindowReady = true;
    if (isAuthReady) {
      showMainWindow();
    }
  }, 2500);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, navigationUrl) => {
    try {
      const parsedUrl = new URL(navigationUrl);
      if (parsedUrl.origin !== 'app://agrivisio') {
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
    const defaultFilename = `AgriVisio-Report-${safeBatchId}.pdf`;
    const { filePath, canceled } = await dialog.showSaveDialog(mainWindow, {
      title: 'Save AgriVisio Assessment Report',
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
    console.error('[AgriVisio Desktop] Failed to save report PDF:', err);
    return { success: false, error: err.message };
  }
});

// IPC handler for renderer desktop session re-authentication
ipcMain.handle('reauthenticate', async () => {
  log('Desktop session recovery requested from renderer...');
  const genAtStart = authGeneration;
  const success = await performElectronBootstrap();
  if (success && currentSessionToken && authGeneration === genAtStart) {
    log('Desktop session recovery successful.');
    return { success: true };
  }
  log('Desktop session recovery failed or cancelled.');
  return { success: false, error: 'Failed to re-authenticate with the optical engine.' };
});

let stopBackendPromise = null;
let hasStoppedBackend = false;

function stopBackend(callback) {
  if (hasStoppedBackend) {
    if (callback) callback();
    return;
  }

  if (!stopBackendPromise) {
    stopBackendPromise = new Promise((resolve) => {
      const proc = pythonProcess;
      if (!proc || proc.killed) {
        pythonProcess = null;
        hasStoppedBackend = true;
        resolve();
        return;
      }
      pythonProcess = null;

      try {
        log('Initiating graceful shutdown of Python worker...');
        if (proc.stdin && !proc.stdin.destroyed) {
          try {
            proc.stdin.write(JSON.stringify({ action: 'shutdown' }) + '\n');
            proc.stdin.end();
          } catch (e) {}
        }

        let finished = false;
        const done = () => {
          if (!finished) {
            finished = true;
            clearTimeout(forceKillTimer);
            hasStoppedBackend = true;
            resolve();
          }
        };

        const forceKillTimer = setTimeout(() => {
          log('Graceful shutdown timeout (3000ms) expired; terminating process forcibly.');
          try {
            if (process.platform === 'win32' && proc.pid) {
              spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { windowsHide: true });
            } else {
              proc.kill('SIGKILL');
            }
          } catch (e) {}
          done();
        }, 3000);

        proc.once('exit', (code, signal) => {
          log(`Python worker exited cleanly (code: ${code}, signal: ${signal}).`);
          done();
        });
      } catch (err) {
        log('Error during stopBackend:', err);
        hasStoppedBackend = true;
        resolve();
      }
    });
  }

  stopBackendPromise.then(() => {
    if (callback) callback();
  });
}

app.whenReady().then(async () => {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.agrivisio.ricequalityanalyser');
  }

  registerProtocolHandler();
  createSplash();
  startPythonWorker();
  createWindow(); // Concurrently pre-renders DOM, CSS, JS, and fonts in background

  const authSuccess = await performElectronBootstrap();
  if (!authSuccess) {
    log('FATAL: Could not establish authenticated session. Aborting launch.');
    dialog.showErrorBox(
      'Authentication Failure',
      'AgriVisio could not establish an authenticated local session with the optical engine.\n\nPlease restart the application.'
    );
    app.quit();
    return;
  }

  isAuthReady = true;
  log(`Authentication handshake complete. isMainWindowReady: ${isMainWindowReady}`);
  if (isMainWindowReady) {
    showMainWindow();
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('before-quit', (e) => {
  log('app before-quit event received. hasStoppedBackend:', hasStoppedBackend);
  if (!hasStoppedBackend) {
    e.preventDefault();
    isQuitting = true;
    stopBackend(() => {
      app.quit();
    });
  }
});

app.on('window-all-closed', () => {
  log('app window-all-closed event received. Total windows:', BrowserWindow.getAllWindows().length);
  if (mainWindow && !mainWindow.isDestroyed()) {
    log('Ignoring window-all-closed because mainWindow is still active.');
    return;
  }
  isQuitting = true;
  if (process.platform !== 'darwin') {
    if (!hasStoppedBackend) {
      stopBackend(() => {
        app.quit();
      });
    } else {
      app.quit();
    }
  }
});
