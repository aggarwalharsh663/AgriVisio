# AgriScan - Optical Grain Quality Assessment & Morphometric Analysis

AgriScan is a computer vision and machine learning platform engineered for automated rice grain inspection, morphometric quantification, and commercial grade classification. The system provides an end-to-end optical pipeline combining color chart calibration, adaptive morphological segmentation, and multi-class grain categorization.

---

## Key Features

- **Standardized Color Calibration**: Corrects ambient light temperature and spectral shifts across daylight, fluorescent tube, and ring-light environments using a 12-patch reference target.
- **Sub-Millimeter Morphometrics**: Extracts individual grain contours to compute calibrated length, width, aspect ratio, projected surface area, and perimeter using ellipse fitting.
- **Ensemble Classifier**: Categorizes grains into standard agronomic quality classes (*Good*, *Broken*, *Chalky*, *Damaged*, *Foreign*) using a trained Random Forest model with class probability estimation.
- **Dual Mode Operation**:
  - **Simulation Chamber**: Software-controlled optical chamber for repeatable synthetic batches, parameter benchmarking, and lighting experiments.
  - **Direct Photo Upload**: Analyzes high-resolution field and laboratory photographs with drag-and-drop ingestion and automatic scale normalization.
- **Audit & Batch Traceability**: SQLite-backed ledger preserving assessment summaries, grain-level records, distribution histograms, and reproducible inspection certificates.
- **Interactive Inspection UI**: Responsive interface supporting both Precision Light and High-Contrast Dark modes, interactive grain sorting, and side-by-side calibration verification.

---

## Architecture & Optical Pipeline

```
[ Image Source: Simulation / Camera Upload ]
                     │
                     ▼
       [ 12-Patch Color Calibration ]
     (Affine / Thin-Plate Spline Balance)
                     │
                     ▼
       [ Contrast Enhancement & CLAHE ]
                     │
                     ▼
      [ Adaptive Watershed Segmentation ]
         (Contour & Mask Extraction)
                     │
                     ▼
        [ Morphometric Quantification ]
   (Length, Width, Aspect Ratio, Color Stats)
                     │
                     ▼
      [ Random Forest Multi-Class Model ]
       (Defect Classification & Confidence)
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
│   ├── main.py               # REST API endpoints and static asset serving
│   ├── model.joblib          # Trained Random Forest classifier artifact
│   ├── simulator.py          # Optical chamber simulation engine
│   └── train_classifier.py   # Training script for classification model
├── frontend/                 # Client interface (vanilla HTML5, CSS3, ES6+)
│   ├── index.html            # Primary application view
│   ├── app.js                # UI state management and API communication
│   ├── chamber.js            # Simulated hardware canvas visualizer
│   ├── style.css             # Design system (Light & Obsidian Cyber Dark)
│   └── vendor/               # Offline libraries (Chart.js, QRCode.js)
├── tests/                    # Automated test suite
│   ├── test_end_to_end.py
│   ├── test_grain_analysis.py
│   ├── test_photo_upload.py
│   ├── test_simulator_calibration.py
│   └── test_validation.py
├── docs/                     # Specifications and contract documentation
│   └── CONTRACT.md           # API and data structure specifications
├── pyproject.toml            # Project build and dependency configuration
├── requirements.txt          # Python package requirements
└── start.bat                 # Windows quickstart launcher script
```

---

## Installation & Setup

### Prerequisites
- Python 3.10 or higher
- Modern web browser (Chrome, Edge, Firefox, or Safari)

### 1. Environment Setup

Clone the repository and create a Python virtual environment:

```powershell
# Navigate to project directory
cd agriscan

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies

Install the required packages from `requirements.txt`:

```powershell
pip install -r requirements.txt
```

### 3. Launch the Application

Start the FastAPI application with Uvicorn:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Or execute the included launcher script:

```powershell
.\start.bat
```

Once running, access the web interface at **`http://localhost:8000`**.

---

## Verification & Testing

AgriScan includes an end-to-end automated test suite verifying optical calibration accuracy, feature extraction bounds, API responses, and database transactions:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

All 24 test suites validate:
- Morphometric measurements against simulated ground truth.
- Color matrix calibration error bounds.
- File upload handling and metadata persistence.
- Random Forest probability scoring and threshold tagging.

---

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Service status check and model warm-up confirmation |
| `POST` | `/api/simulate` | Generates a synthetic grain batch with chosen lighting preset |
| `POST` | `/api/upload` | Ingests a user-supplied grain photograph |
| `POST` | `/api/assess` | Executes the complete segmentation, measurement, and classification pipeline |
| `GET` | `/api/batches` | Retrieves list of recorded historical batches |
| `GET` | `/api/batches/{id}` | Fetches detailed metrics and grain coordinates for a specific batch |
| `GET` | `/api/batches/{id}/export` | Exports batch report data in JSON format |

---

## Quality Metrics & Standards

- **Length / Width**: Measured in millimetres using calibrated optical scale factors.
- **Grain Classifications**:
  - `good`: Intact, translucent, well-formed grain meeting length-to-width ratio specifications.
  - `broken`: Grains fractured below standardized length thresholds (< 75% of whole grain length).
  - `chalky`: Grains exhibiting opaque, starchy white discoloration covering > 20% surface area.
  - `damaged`: Discolored, speckled, or heat-damaged kernels.
  - `foreign`: Non-grain particulate matter or extraneous material.
- **Confidence Scoring**: Model class posterior probability. Samples with borderline probabilities are logged for quality control verification.
