# AgriScan Security Architecture & Threat Model

## Overview

AgriScan is an optical grain assessment desktop and API application. This document outlines the security architecture, threat model, authentication mechanisms, file containment policies, and cryptographic verification controls implemented across the system.

---

## 1. Authentication Architecture & Session Lifecycle

### Core Authentication Channels
1. **Desktop Portless IPC (Primary Desktop Path)**:
   - When running as an Electron desktop application, AgriScan does not expose loopback network ports for internal communication.
   - Electron communicates with the Python computer vision engine (`backend/ipc_worker.py`) using standard input and output pipes (`stdio`), bypassing the network stack entirely.
2. **Local HTTP API & Web Interface**:
   - FastAPI binds strictly to `127.0.0.1` (loopback).
   - Protected API endpoints (`/api/scan`, `/api/scan/upload`, `/api/batches`, `/files/...`) require authentication.
   - Permitted authentication formats:
     - Header: `X-AgriScan-Token: <token>`
     - Header: `Authorization: Bearer <token>`
     - Cookie: `agriscan_session=<token>`
   - **Query-token authentication (`?token=...`) is strictly removed and prohibited** to prevent secret leakage in access logs, proxy logs, and browser navigation histories.

### Secure Single-Use Bootstrap Protocol (R-02 / S-01 / S-02)
To prevent unauthorized loopback processes from discovering or extracting API credentials:
1. **Generation**: The Electron main process generates a cryptographically secure 32-byte hexadecimal secret at startup using `crypto.randomBytes(32).toString('hex')`.
2. **Registration**: Electron delivers the secret to the Python backend over the protected, portless `stdio` IPC pipe via a `register_bootstrap` message.
3. **Storage & Expiration**: The backend registers the secret in memory with a 30-second TTL (`BOOTSTRAP_SECRET_TTL = 30.0`). Expired and consumed entries are purged automatically during registration and validation cycles.
4. **Exchange**: Electron performs the bootstrap exchange over IPC (`bootstrap`) or via `POST /api/auth/bootstrap`. The backend validates the secret:
   - Secret must match an active in-memory record.
   - Secret must be unexpired.
   - Secret must not have been previously consumed.
   - Secret is consumed atomically and immediately deleted to prevent replay attacks.
5. **Session Issuance & Token Secrecy**:
   - The session is established by issuing an `HttpOnly` cookie (`agriscan_session`).
   - The HTTP response body contains **only** `{"status": "authenticated"}`. The master API token is **never** disclosed in the HTTP response body or exposed to renderer JavaScript.
6. **No Free Sessions on Static Resources (S-01)**:
   - Static asset delivery (`/`, `/index.html`, `/app.js`, `/style.css`) does not issue cookies or grant authenticated sessions. An unauthenticated visitor receives static assets but cannot invoke protected APIs or download batch inspection records.

### Cookie Security Policy
- `HttpOnly: True` — Inaccessible to JavaScript and DOM environments (`document.cookie`), mitigating XSS session theft.
- `SameSite: Lax` — Protects against Cross-Site Request Forgery (CSRF).
- `Path: /` — Scoped to application boundaries.
- `Max-Age: 86400` (24-hour expiration policy).
- `Secure: True` over HTTPS; explicitly documented as `False` when bound to local `http://127.0.0.1` to ensure browser loopback compatibility.
- Session termination endpoint `POST /api/auth/logout` explicitly deletes the cookie.

---

## 2. File-Serving Security (`/files`) (R-01 / Additional 2)

### Access Control
- Unrestricted directory exposure has been eliminated.
- All requests under `/files/...` strictly enforce authentication via `verify_api_token` (returning `HTTP 401 Unauthorized` for unauthenticated callers).

### Narrowly Scoped Containment
- Requests must match approved batch directory roots:
  - `batches/<valid-batch-id>/<filename>`
  - Legacy `AS-.../<filename>`
  - Batch identifiers must match alphanumeric/standard batch naming formats (`^[\w-]+$`). Unscoped paths outside batch roots return `HTTP 403 Forbidden`.
- Permitted extensions are strictly restricted to: `[".jpg", ".jpeg", ".png", ".webp", ".json"]`.
- The following sensitive extensions are unconditionally rejected with `HTTP 403 Forbidden`:
  `[".sqlite3", ".db", ".auth_token", ".py", ".pyc", ".joblib", ".env", ".key", ".pem", ".log", ".bat", ".cmd", ".ps1", ".sh", ".webm", ".mp4", ".exe", ".dll", ".ini"]`.
- Traversal sequences (`..`), percent-encoded equivalents (`%2e%2e`), Windows path separators (`\`), and null bytes (`%00`) are blocked before filesystem resolution.
- Symbolic links and junction points are validated with canonical realpath prefix containment checks (`str(target_path).startswith(str(base_dir) + os.sep)`).

---

## 3. Electron Desktop Hardening (R-06 / S-02)

### Content Security Policy (CSP) Coverage
- HTML documents served through the custom `app://` scheme explicitly enforce a Content Security Policy header:
```
default-src 'self' app:; script-src 'self' app:; style-src 'self' 'unsafe-inline' app: https://fonts.googleapis.com; font-src 'self' app: https://fonts.gstatic.com data:; img-src 'self' app: data: blob:; connect-src 'self' app: http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self' app:; frame-ancestors 'none';
```
- Non-HTML responses (such as images, fonts, stylesheet files, and JSON API payloads) return appropriate content-type headers without document-specific CSP directives.

### Process Isolation & WebPreferences
`mainWindow` enforces:
- `nodeIntegration: false`
- `contextIsolation: true`
- `sandbox: true`
- `webSecurity: true`
- `allowRunningInsecureContent: false`
- `navigateOnDragDrop: false`
- Navigation restrictions: `will-navigate` blocks navigation away from `app://agriscan`; external links open via `shell.openExternal`; `will-attach-webview` prevents untrusted webviews.

### Graceful Child Process Termination
Forced `taskkill` has been replaced by a graceful multi-step shutdown:
1. Electron issues a graceful `{"action": "shutdown"}` message over the child's `stdin` pipe and closes the stream.
2. The Python worker cleans up active SQLite WAL logs, closes handles, and terminates with code 0.
3. A 3000ms watchdog timer falls back to direct process termination (`spawn('taskkill', ...)` without shell invocation) only if the child process hangs.

---

## 4. Machine Learning Model Integrity (F-04 / F-07)

- The classification model (`backend/model.joblib`) is verified prior to deserialization.
- Expected SHA-256 is pinned in `backend/model_manifest.json` (`90ad4a263d28f5d1414ede8241751c9dc2949e60966857acfdfc781d22489dfa`).
- If the model file is altered, missing, or mismatched, `ModelIntegrityError` is raised and the application fails closed.
- Model inferences force `n_jobs = 1` on desktop to eliminate loky multiprocessing worker deadlocks.
- The system operates on four reconciled agronomic classes: `good`, `broken`, `chalky`, `discolored`.

---

## 5. Mock Mode Isolation (R-07)

- Mock simulation mode is **strictly OFF by default**.
- Enabling mock mode requires explicit configuration:
  `AGRISCAN_ENV=development` AND `MOCK=1`.
- If `AGRISCAN_ENV=production`, mock simulation is rejected with `HTTP 403 Forbidden`.
- Mock inspection records are tagged with `is_mock: true`, `simulated: true`, and prominent UI warning watermarks.
- Exporting mock data as official inspection certificates is blocked by `GET /api/batches/{batch_id}/export`.

---

## 6. Installer Integrity Verification & Packaging Security (R-05 / S-03 / Additional 1)

### Fail-Closed Constant-Time Payload Verification
`AgriScanSetup.exe` contains a stamped SHA-256 checksum (`ExpectedPayloadSha256`). During installation:
1. The embedded payload stream (`agriscan_payload.zip`) is read.
2. The SHA-256 hash is computed in memory.
3. Hash verification enforces:
   - `ExpectedPayloadSha256` must be non-empty and a valid 64-character hexadecimal string.
   - Computed hash is compared against expected hash using a constant-time algorithm (`FixedTimeHexEquals`) iterating over all characters without early return.
   - If empty, invalid, or mismatched, a `SecurityException` is thrown and installation halts immediately.

### Build-Time Packaging Security (Additional 1)
- The installer build script (`scripts/build_installer.py`) enforces strict exclusion patterns blocking `.env`, `.env.*`, credential files, and private keys (`*.key`, `*.pem`, `*.token`, `.auth_token`).
- Safe templates (`.env.example`) are preserved.
- A build-time secret scan searches payload source files for embedded private keys and fails closed if detected.

---

## 7. Threat Boundaries & Honest Security Limitations

1. **Same-User Operating System Boundary**: Like standard desktop software, local loopback tokens and named pipes provide isolation from unauthorized web origins and unprivileged network traffic. However, they do not isolate against other processes executing under the same OS user account with memory inspection or file access privileges.
2. **Physical Machine Learning Calibration**: Model classifications achieve 42.1% empirical accuracy on standardized synthetic simulation scenes. Real-world photographs require physical camera calibration and are explicitly tagged with an "UNCALIBRATED MEASUREMENT" advisory when captured without a physical optical reference target (see `docs/report_validation.md`).
