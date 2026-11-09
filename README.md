# AgriScan - Precision Optical Grain Assessment & Morphometric Analysis

AgriScan is an enterprise computer vision and machine learning platform engineered for automated rice grain inspection, sub-millimeter morphometric quantification, and commercial grade classification. The system provides an end-to-end optical pipeline combining color reference calibration, adaptive morphological segmentation, and four-class grain defect categorization.

---

## Key Features

- **Standardized Color Calibration**: Corrects ambient light temperature and spectral shifts across daylight, fluorescent tube, and ring-light environments using a 12-patch reference target with thin-plate spline / affine balancing.
- **Sub-Millimeter Morphometrics**: Extracts individual grain contours using adaptive watershed segmentation to compute calibrated length, width, aspect ratio, projected surface area, and perimeter using ellipse fitting.
- **Cryptographic Model Verification**: Random Forest classifier artifact verified against SHA-256 integrity manifest prior to deserialization to prevent tampering and arbitrary code execution.
- **Agronomic 4-Class Categorization**: Accurately classifies grains into standard agronomic quality classes (*Good*, *Broken*, *Chalky*, *Discolored*) with class probability estimation.
- **Zero-Port Desktop IPC**: Electron desktop integration communicates directly with the Python computer vision worker through secure stdin/stdout pipes and the custom `app://` protocol with strict path traversal containment and Content-Security-Policy enforcement.
- **Secure File Serving**: Authenticated, narrowly scoped batch file serving protecting all batch artifacts, images, and inspection ledgers.
- **Fail-Closed Windows Installer**: Automated SHA-256 payload integrity verification rejecting corrupted or altered installation packages.
- **Audit & Cryptographic Fingerprinting**: Deterministic multi-factor SHA-256 hashing binding morphometrics, class distribution, and image identity for tamper-evident inspection certificates.
- **Interactive Inspection UI**: Responsive interface supporting both Precision Light and High-Contrast Dark modes, interactive grain sorting, and side-by-side calibration verification.

---

## Architecture & Optical Pipeline

```
[ Image Source: Simulation / Photo Upload / Scanner ]
                          │
                          ▼
             [ Reference Calibration ]
        (12-Patch Thin-Plate Spline Balance)
                          │
                          ▼
            [ Adaptive Watershed Segmentation ]
               (Contour & Mask Extraction)
                          │
                          ▼
              [ Morphometric Extraction ]
        (Length, Width, Aspect Ratio, Color Stats)
                          │
                          ▼
         [ Verified Random Forest Classifier ]
             (SHA-256 Validated Joblib Model)
                          │
                          ▼
      [ Batch Aggregation & SQLite Ledger ]
```

---

## Project Structure

```
agriscan/
├── backend/                  # FastAPI service and computer vision engine
│   ├── calibration.py        # Color patch detection and matrix balancing
│   ├── database.py           # SQLite persistence layer and batch schema
│   ├── fingerprint.py        # Unique batch identification hashing
│   ├── grain_analysis.py     # Segmentation, feature extraction, and ML inference
│   ├── ipc_worker.py         # Portless stdin/stdout IPC bridge for Electron
│   ├── main.py               # REST API endpoints and token authentication
│   ├── model.joblib          # Trained Random Forest classifier artifact
│   ├── model_manifest.json   # Cryptographic SHA-256 integrity manifest
│   ├── simulator.py          # Optical chamber simulation engine
│   └── train_classifier.py   # Training script for classification model
├── electron/                 # Electron desktop wrapper
│   ├── main.cjs              # Zero-port IPC bridge & protocol handler
│   ├── package.json          # Pinned desktop packaging configuration
│   └── package-lock.json     # Locked npm dependency manifest
├── frontend/                 # Client interface (vanilla HTML5, CSS3, ES6+)
│   ├── index.html            # Primary application view
│   ├── app.js                # UI state management and API communication
│   ├── chamber.js            # Simulated hardware canvas visualizer
│   ├── style.css             # Design system (Light & Obsidian Cyber Dark)
│   └── vendor/               # Offline libraries (Chart.js, QRCode.js)
├── scripts/                  # Build and release tooling
│   └── build_installer.py    # Automated installer builder with SHA-256 verification
├── tests/                    # Automated test suite
│   ├── test_end_to_end.py
│   ├── test_grain_analysis.py
│   ├── test_ipc_worker.py
│   ├── test_photo_upload.py
│   ├── test_security_regressions.py
│   ├── test_simulator_calibration.py
│   └── test_validation.py
├── validation/               # Model evaluation scripts and benchmarks
│   ├── validate.py           # Ground-truth matching and metric calculation
│   ├── validate_model.py     # Reproducible model evaluation runner
│   └── run_experiment.py     # Multi-lighting benchmark runner
├── docs/                     # Specifications and contract documentation
│   ├── CONTRACT.md           # API and data structure specifications
│   ├── report_validation.md  # Formal model validation report
│   ├── security.md           # Security architecture and threat model
│   └── remediation_report.md # Security remediation audit report
├── AgriScan.exe              # Native Windows launcher
├── AgriScanSetup.exe         # Verified Windows installer binary
├── setup_form.cs             # C# installer source with fail-closed SHA-256 check
├── requirements.txt          # Exact pinned Python package dependencies
├── start.bat                 # Windows quickstart launcher script
└── LICENSE                   # Apache 2.0 open-source license
```

---

## Installation & Setup

### Prerequisites
- Python 3.10 to 3.13
- Node.js 18+ (for Electron desktop packaging)
- Windows 10/11 x64 (for native installer and desktop binary)

### 1. Python Environment Setup
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install exact pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Electron Desktop Setup
```powershell
cd electron
npm ci
cd ..
```

### 3. Launching AgriScan Desktop
Run the native launcher directly:
```powershell
.\AgriScan.exe
```
Or execute the automated batch launcher:
```powershell
.\AgriScan.bat
```

---

## Security Architecture & Configuration

### Authentication Flow
AgriScan implements a multi-tier authentication model:
1. **Desktop IPC Mode**: Electron communicates directly with the Python computer vision worker over protected standard input/output pipes (`stdio`), eliminating local network listening ports.
2. **Browser / Web API Mode**: Protected endpoints require authentication via:
   - `X-AgriScan-Token: <token>` HTTP header, or
   - `Authorization: Bearer <token>` HTTP header, or
   - `agriscan_session` cookie (`HttpOnly=True`, `SameSite=Lax`, `Path=/`).
   - *Query-token authentication (`?token=...`) is strictly rejected* to prevent token exposure in URLs, browser history, or logs.
3. **One-Time Bootstrap Protocol**: Arbitrary localhost HTTP callers cannot retrieve master API tokens. When initializing sessions, clients exchange a single-use, 30-second ephemeral bootstrap secret via `POST /api/auth/bootstrap`.

### File-Serving Security (`/files`)
Batch inspection records, report JSONs, and high-resolution photographs are served through authenticated, narrowly scoped endpoints (`/files/batches/{batch_id}/{filename}`). Unauthenticated requests return HTTP 401. Directory traversal (`..`), percent-encoded traversals (`%2e`), null bytes (`%00`), and sensitive system files (`.sqlite3`, `.db`, `.auth_token`, `.py`, `.joblib`, `.env`) are strictly blocked.

### Content Security Policy (CSP)
Electron enforces a strict CSP on `app://agriscan`:
```
default-src 'self' app:; script-src 'self' app:; style-src 'self' 'unsafe-inline' app: https://fonts.googleapis.com; font-src 'self' app: https://fonts.gstatic.com data:; img-src 'self' app: data: blob:; connect-src 'self' app: http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self' app:; frame-ancestors 'none';
```
`bypassCSP` is disabled in scheme registration.

### Mock Simulation Mode Configuration
Mock simulation mode is **strictly OFF by default** to prevent confusion with genuine laboratory measurements.
To intentionally enable mock simulation for testing, developers must explicitly set **both**:
```powershell
$env:AGRISCAN_ENV = "development"
$env:MOCK = "1"
```
If `AGRISCAN_ENV=production`, mock mode is unconditionally rejected with HTTP 403 Forbidden. Mock batches are watermarked and cannot be exported as official certificates.

---

## Building the Windows Installer

AgriScan includes an automated build script that packages runtime components into an embedded payload ZIP, computes its cryptographic SHA-256 hash, stamps the hash into `setup_form.cs`, and compiles `AgriScanSetup.exe` with `csc.exe`:

```powershell
python scripts/build_installer.py
```

The resulting `AgriScanSetup.exe` verifies the embedded payload against the stamped SHA-256 before extraction and fails closed if any byte has been modified or corrupted.

---

## Running Automated Tests

Execute the complete automated test suite (63 unit, integration, and security regression tests):
```powershell
python -m pytest tests/ -v
```

Execute the reproducible model validation benchmark:
```powershell
python validation/validate_model.py --grains 60
```

---

## Known Limitations

1. **Local User Boundary**: Local loopback authentication protects against cross-origin browser attacks and unprivileged network calls, but does not isolate against malicious processes running under the identical operating-system user profile with direct memory access.
2. **Real-World Model Certification**: Agronomic defect classification is validated empirically against synthetic ground-truth scenes. Physical laboratory certification remains pending larger real-world photographic benchmark datasets (see `docs/report_validation.md`).
3. **Hardware Code Signing**: Shipped installer binaries are self-verified via cryptographic SHA-256 hash checking; production commercial distributions require an Authenticode EV code-signing certificate to suppress Windows SmartScreen warnings.
