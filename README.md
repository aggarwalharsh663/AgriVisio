# AgriScan - Precision Optical Grain Assessment & Morphometric Analysis

AgriScan is an enterprise computer vision and machine learning platform engineered for automated rice grain inspection, sub-millimeter morphometric quantification, and commercial grade classification. The system provides an end-to-end optical pipeline combining color reference calibration, adaptive morphological segmentation, and four-class grain defect categorization.

---

## Key Features

- **Standardized Color Calibration**: Corrects ambient light temperature and spectral shifts across daylight, fluorescent tube, and ring-light environments using a 12-patch reference target with thin-plate spline / affine balancing.
- **Sub-Millimeter Morphometrics**: Extracts individual grain contours using adaptive watershed segmentation to compute calibrated length, width, aspect ratio, projected surface area, and perimeter using ellipse fitting.
- **Cryptographic Model Verification**: Random Forest classifier artifact verified against SHA-256 integrity manifest prior to deserialization to prevent tampering and arbitrary code execution.
- **Agronomic 4-Class Categorization**: Accurately classifies grains into standard agronomic quality classes (*Good*, *Broken*, *Chalky*, *Discolored*) with class probability estimation.
- **Zero-Port Desktop IPC**: Electron desktop integration communicates directly with the Python computer vision worker through secure stdin/stdout pipes and the custom `app://` protocol with strict path traversal containment.
- **Dual Mode Operation**:
  - **Simulation Chamber**: Software-controlled optical chamber for repeatable synthetic batches, parameter benchmarking, and lighting experiments.
  - **Direct Photo Upload**: Analyzes high-resolution field and laboratory photographs with physical millimeter calibration support.
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
│   └── package.json          # Desktop packaging configuration
├── frontend/                 # Client interface (vanilla HTML5, CSS3, ES6+)
│   ├── index.html            # Primary application view
│   ├── app.js                # UI state management and API communication
│   ├── chamber.js            # Simulated hardware canvas visualizer
│   ├── style.css             # Design system (Light & Obsidian Cyber Dark)
│   └── vendor/               # Offline libraries (Chart.js, QRCode.js)
├── tests/                    # Automated test suite
│   ├── test_end_to_end.py
│   ├── test_grain_analysis.py
│   ├── test_ipc_worker.py
│   ├── test_photo_upload.py
│   ├── test_simulator_calibration.py
│   └── test_validation.py
├── validation/               # Model evaluation scripts and benchmarks
│   ├── validate.py           # Ground-truth matching and metric calculation
│   ├── validate_model.py     # Reproducible model evaluation runner
│   └── run_experiment.py     # Multi-lighting benchmark runner
├── docs/                     # Specifications and contract documentation
│   ├── CONTRACT.md           # API and data structure specifications
│   └── report_validation.md  # Formal model validation report
├── AgriScan.exe              # Native Windows launcher
├── requirements.txt          # Pinned Python package dependencies
├── start.bat                 # Windows quickstart launcher script
└── LICENSE                   # Apache 2.0 open-source license
```

---

## Installation & Setup

### Prerequisites
- Python 3.10 to 3.13
- Modern web browser (Chrome, Edge, Firefox, or Safari) or Electron desktop runtime

### 1. Environment Setup

Clone the repository and set up a Python virtual environment:

```powershell
# Navigate to project directory
cd agriscan

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

Install the pinned, verified dependencies from `requirements.txt`:

```powershell
pip install -r requirements.txt
```

### 3. Launch the Application

#### A. Launch as Standalone Windows Desktop App (Zero Network Ports)
Launch the compiled Windows desktop binary:
```powershell
.\AgriScan.exe
```
Or run directly through Electron:
```powershell
cd electron
npx electron .
```

#### B. Launch in Web Browser Mode
Start the FastAPI backend with Uvicorn bound to loopback:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Or execute the included launcher script:

```powershell
.\start.bat
```

Once running, access the web interface at **`http://127.0.0.1:8000`**.

---

## Verification & Testing

AgriScan includes an end-to-end automated test suite verifying optical calibration accuracy, feature extraction bounds, API responses, IPC protocols, and security controls:

```powershell
python -m pytest -v
```

To run model validation across all lighting conditions:

```powershell
python validation/validate_model.py --grains 60
```

---

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Service status check, worker health, and model integrity confirmation |
| `GET` | `/api/auth/token` | Retrieve local loopback session token |
| `POST` | `/api/scan` | Executes optical chamber simulation scan with chosen lighting preset |
| `POST` | `/api/scan/upload` | Ingests and analyzes a user-supplied grain photograph |
| `GET` | `/api/batches` | Retrieves paginated list of recorded historical batches |
| `GET` | `/api/batches/{id}` | Fetches detailed metrics and grain coordinates for a specific batch |
| `GET` | `/api/batches/{id}/export` | Exports verified batch report data in JSON format |

---

## Quality Metrics & Standards

- **Length / Width**: Calibrated in millimetres using optical reference targets.
- **Grain Classifications**:
  - `good`: Intact, translucent, well-formed grain meeting aspect-ratio specifications.
  - `broken`: Grains fractured below standardized length thresholds (< 75% of whole grain length).
  - `chalky`: Grains exhibiting opaque, starchy white discoloration covering > 20% surface area.
  - `discolored`: Grains showing dark spots, fungal staining, or heat discoloration.
- **Confidence Scoring**: Model posterior probability computed for each grain. Borderline detections are highlighted for operator inspection.
