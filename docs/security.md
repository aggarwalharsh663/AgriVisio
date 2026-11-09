# AgriScan Security Architecture & Threat Model

## Overview

AgriScan is an optical grain assessment desktop and API application. This document outlines the security architecture, threat model, authentication mechanisms, file containment policies, and cryptographic verification controls implemented across the system.

---

## 1. Authentication Architecture & Session Lifecycle

### Core Authentication Channels
1. **Desktop Portless IPC (Primary Desktop Path)**:
   - When running as an Electron desktop application, AgriScan does not bind to public network interfaces.
   - Electron communicates with the Python computer vision engine (`backend/ipc_worker.py`) using standard input and output pipes (`stdio`), bypassing the network stack entirely.
2. **Local HTTP API & Web Interface**:
   - FastAPI binds strictly to `127.0.0.1` (loopback).
   - Protected API endpoints (`/api/scan`, `/api/scan/upload`, `/api/batches`, `/files/...`) require authentication.
   - Permitted authentication formats:
     - Header: `X-AgriScan-Token: <token>`
     - Header: `Authorization: Bearer <token>`
     - Cookie: `agriscan_session=<token>`
   - **Query-token authentication (`?token=...`) is strictly removed and prohibited** to prevent secret leakage in access logs, proxy logs, and browser navigation histories.

### Secure Single-Use Bootstrap Protocol (R-02)
To eliminate unauthorized loopback credential discovery (formerly `GET /api/auth/token`):
1. A 32-byte cryptographic bootstrap secret is generated (via `secrets.token_hex(32)`) or passed via secure process environment (`AGRISCAN_BOOTSTRAP_SECRET`).
2. The client presents the bootstrap secret via `POST /api/auth/bootstrap`.
3. The server validates the secret against an in-memory registry:
   - Secret must be unexpired (30-second TTL).
   - Secret must be unconsumed.
   - Upon successful verification, the secret is immediately marked as consumed and deleted, preventing replay attacks.
4. The server issues an authenticated session response setting an `HttpOnly` session cookie.

### Cookie Security Policy
- `HttpOnly: True` — Inaccessible to JavaScript and DOM environments (`document.cookie`), mitigating XSS session theft.
- `SameSite: Lax` — Protects against Cross-Site Request Forgery (CSRF).
- `Path: /` — Scoped to application boundaries.
- `Max-Age: 86400` (24-hour expiration policy).
- `Secure: True` over HTTPS; explicitly documented as `False` when bound to local `http://127.0.0.1` to ensure browser loopback compatibility.
- Session termination endpoint `POST /api/auth/logout` explicitly deletes the cookie.

---

## 2. File-Serving Security (`/files`) (R-01)

### Access Control
- Unrestricted directory exposure has been eliminated.
- All requests under `/files/...` strictly enforce authentication via `verify_api_token`.
- Unauthenticated requests return `HTTP 401 Unauthorized`.

### Narrowly Scoped Containment
- Requests must correspond to legitimate batch artifacts.
- Permitted extensions are restricted to: `[".jpg", ".jpeg", ".png", ".webp", ".json"]`.
- The following sensitive extensions are unconditionally rejected with `HTTP 403 Forbidden`:
  `[".sqlite3", ".db", ".auth_token", ".py", ".pyc", ".joblib", ".env", ".key", ".pem", ".log", ".bat", ".cmd", ".ps1", ".sh", ".webm", ".mp4", ".exe", ".dll", ".ini"]`.
- Traversal sequences (`..`), percent-encoded equivalents (`%2e%2e`), Windows path separators (`\`), and null bytes (`%00`) are blocked before filesystem resolution.
- Symbolic links and junction points are validated with canonical realpath prefix containment checks (`str(target_path).startswith(str(base_dir) + os.sep)`).

---

## 3. Electron Desktop Hardening (R-06)

### Content Security Policy (CSP)
Electron registers the custom `app://` protocol without `bypassCSP: true`. All served pages enforce the following policy:
```
default-src 'self' app:;
script-src 'self' app:;
style-src 'self' 'unsafe-inline' app: https://fonts.googleapis.com;
font-src 'self' app: https://fonts.gstatic.com data:;
img-src 'self' app: data: blob:;
connect-src 'self' app: http://127.0.0.1:* http://localhost:*;
object-src 'none';
base-uri 'self' app:;
frame-ancestors 'none';
```

### Process Isolation & WebPreferences
`mainWindow` enforces:
- `nodeIntegration: false`
- `contextIsolation: true`
- `sandbox: true`
- `webSecurity: true`
- `allowRunningInsecureContent: false`
- `navigateOnDragDrop: false`
- Navigation restrictions: `will-navigate` listener blocks external navigations; `will-attach-webview` prevents untrusted webviews.

### Graceful Child Process Termination
Forced `taskkill` has been replaced by a graceful multi-step shutdown:
1. Electron issues a graceful `{"action": "shutdown"}` message over the child's `stdin` pipe and closes the stream.
2. The Python worker cleans up active SQLite WAL logs, closes handles, and terminates with code 0.
3. A 3000ms watchdog timer falls back to process termination only if the child process hangs.

---

## 4. Machine Learning Model Integrity (F-04)

- The classification model (`backend/model.joblib`) is verified prior to deserialization.
- Expected SHA-256 is pinned in `backend/model_manifest.json` (`90ad4a263d28f5d1414ede8241751c9dc2949e60966857acfdfc781d22489dfa`).
- If the model file is altered, missing, or mismatched, `ModelIntegrityError` is raised and the application fails closed.
- Model inferences force `n_jobs = 1` on desktop to eliminate loky multiprocessing worker deadlocks.

---

## 5. Mock Mode Isolation (R-07)

- Mock simulation mode is **strictly OFF by default**.
- Enabling mock mode requires explicit configuration:
  `AGRISCAN_ENV=development` AND `MOCK=1`.
- If `AGRISCAN_ENV=production`, mock simulation is rejected with `HTTP 403 Forbidden`.
- Mock inspection records are tagged with `is_mock: true`, `simulated: true`, and prominent UI warning watermarks.
- Exporting mock data as official inspection certificates is blocked by `GET /api/batches/{batch_id}/export`.

---

## 6. Installer Integrity Verification & Code Signing (R-05)

### Fail-Closed Payload Verification
`AgriScanSetup.exe` contains a stamped SHA-256 checksum (`ExpectedPayloadSha256`). During installation:
1. The embedded payload stream (`agriscan_payload.zip`) is read.
2. The SHA-256 hash is computed in memory.
3. The installer checks:
   - `ExpectedPayloadSha256` is non-empty and a valid 64-character hexadecimal string.
   - Computed hash equals expected hash in constant time.
   - If empty, invalid, or mismatched, a `SecurityException` is thrown and installation halts immediately.

### Code Signing Workflow
For production Windows distribution, release executables (`AgriScan.exe` and `AgriScanSetup.exe`) should be signed with an Authenticode code-signing certificate using Microsoft SignTool:
```cmd
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a "AgriScanSetup.exe"
signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 /a "AgriScan.exe"
```
Verification command:
```cmd
signtool verify /pa /v "AgriScanSetup.exe"
```

---

## 7. Known Limitations & Threat Boundaries

1. **Same-User Local Boundary**: Like standard desktop software, local loopback tokens do not prevent access from other processes executing under the exact same Windows user account with debugger or memory inspection privileges.
2. **Physical Machine Learning Certification**: The classification model is validated empirically against synthetic ground-truth images and requires further empirical benchmarking before commercial or laboratory certification (see `docs/report_validation.md`).
