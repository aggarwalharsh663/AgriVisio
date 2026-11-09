# AgriScan Model Validation & Accuracy Report

## Executive Summary

This report establishes the empirical validation methodology and performance benchmarks for the AgriScan grain assessment platform. It reconciles agronomic defect categories, details performance across optical lighting presets, evaluates measurement errors, and formally distinguishes simulated chamber experiments from real-world photograph analysis.

---

## 1. Agronomic Quality Classification

### Reconciling Four Core Grain Classes
AgriScan implements **four** distinct morphological and photometric grain quality classes:
1. **Good (`good`)**: Intact, translucent whole rice grains meeting typical variety aspect-ratio and dimension thresholds.
2. **Broken (`broken`)**: Fractured grains with length significantly below whole grain expectations ($< 75\%$ standard whole grain length).
3. **Chalky (`chalky`)**: Grains exhibiting opaque, starchy white chalkiness exceeding $20\%$ of projected surface area.
4. **Discolored (`discolored`)**: Kernels displaying pigmentation defects, dark spots, fungal staining, or heat damage.

> **Correction of Prior Class Discrepancies**:
> Early documentation drafts erroneously cited a 5th category (*"Foreign"* or *"Damaged"*). The deployed Random Forest model (`model.joblib`), the training scripts (`train_classifier.py`), the validation suite (`validate.py`), and the database schema operate strictly on the four calibrated classes: `['good', 'broken', 'chalky', 'discolored']`. Extraneous claims of a 5th class have been retracted to maintain specification integrity.

---

## 2. Experimental Methodology

The automated validation suite (`validation/validate_model.py` and `validation/run_experiment.py`) evaluates 15 multi-scene scans across five standardized seeds (42–46) under three simulated illumination environments:
- **`ring_light`**: Diffuse overhead circular illumination.
- **`daylight`**: Broad-spectrum ambient lighting.
- **`tube_light`**: Linear fluorescent lighting with directional shadowing.

For each scan, synthetic scenes are generated with known ground-truth grain dimensions, positions, and labels. The end-to-end pipeline executes:
1. Reference color-card patch detection and thin-plate spline / affine calibration.
2. Image preprocessing and adaptive watershed contour segmentation.
3. Feature extraction (calibrated length, width, aspect ratio, RGB and HSV statistics, chalky area fraction).
4. Random Forest inference with probability distribution.
5. Nearest-center bipartite ground-truth matching ($\text{threshold} \le 20\,\text{px}$).

---

## 3. Empirical Validation Results

### Benchmark Metrics Summary
| Simulated Lighting | Accuracy Mean | Accuracy Sample SD | Length MAE (mm) | Good P / R | Broken P / R | Chalky P / R | Discolored P / R |
|---|---|---|---|---|---|---|---|
| `ring_light` | **44.3%** | $\pm 3.4\%$ | 0.142 mm | 1.00 / 0.17 | 1.00 / 1.00 | 0.17 / 1.00 | 1.00 / 1.00 |
| `tube_light` | **43.0%** | $\pm 3.1\%$ | 0.138 mm | 0.97 / 0.15 | 1.00 / 1.00 | 0.17 / 1.00 | 1.00 / 0.96 |
| `daylight`   | **39.0%** | $\pm 1.6\%$ | 0.143 mm | 1.00 / 0.09 | 1.00 / 1.00 | 0.16 / 1.00 | 1.00 / 1.00 |
| **Combined** | **42.1%** | $\pm 3.3\%$ | **0.141 mm** | — | — | — | — |

*P / R denotes Precision / Recall.*

### Analysis of Classifier Dynamics
- **High Broken & Discolored Fidelity**: The model achieves near 100% precision and recall for `broken` and `discolored` grains due to stark morphological aspect ratio differences and chrominance shifts.
- **Chalky vs Good Disambiguation**: The classifier displays a conservative bias, classifying borderline translucent grains as `chalky` under variable light dispersion. This yields high recall (1.00) but lower precision (~0.17) for `chalky` grains, while `good` grains maintain 100% precision with lower recall (~0.15).
- **Sub-Millimeter Morphometric Precision**: Across all 15 benchmark runs, the mean absolute error for grain length is **0.141 mm**, well within commercial grading tolerances ($\pm 0.25\,\text{mm}$).

---

## 4. Physical Photography vs. Simulated Chamber

It is critical to distinguish between synthetic chamber benchmarks and physical grain photographs:
1. **Simulated Chamber (`simulated: True`)**:
   - Hardware lighting, grain dispersion, and camera optics are simulated via software.
   - Ground truth coordinates and classifications are synthetically generated for calibration benchmarking.
   - Marked clearly with `is_mock: False`, `simulated: True` for algorithmic testing.
2. **Physical Camera / Uploaded Photographs (`simulated: False`)**:
   - Ingested via direct photo upload or WIA scanner interface.
   - Requires physical millimeter-per-pixel calibration (or explicit calibration card in frame).
   - In the absence of an optical reference target, the system applies an assumed scale ($0.12\,\text{mm/px}$) and explicitly tags reports with an **"UNCALIBRATED MEASUREMENT"** advisory banner.
   - Grain defect classifications are predicted by the machine learning model, but synthetic ground truth comparison is disabled (`validation: N/A`).

---

## 5. Model Integrity & Provenance

To prevent arbitrary code execution and model tampering:
- The classifier artifact (`backend/model.joblib`) is verified against its cryptographic manifest (`backend/model_manifest.json`) before deserialization.
- Expected SHA-256: `90ad4a263d28f5d1414ede8241751c9dc2949e60966857acfdfc781d22489dfa`.
- Any mismatch or missing manifest causes the service to fail closed (`ModelIntegrityError`). Silent fallback to unverified heuristic models is prohibited.
- Reproducible validation can be re-run at any time using:
  ```powershell
  python validation/validate_model.py --grains 60
  ```
