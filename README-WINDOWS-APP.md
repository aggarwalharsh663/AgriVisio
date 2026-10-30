# AgriScan - Windows Desktop Application

This is the standalone **Windows Desktop Application** conversion of **AgriScan** (Optical Grain Quality Assessment & Morphometric Analysis Platform).

---

## 🚀 How to Run the App

You have three easy ways to launch the Windows app:

1. **Desktop Shortcut**:
   Double-click the **`AgriScan`** shortcut directly on your Windows Desktop.

2. **Native Executable**:
   Double-click **`AgriScan.exe`** in this folder (`AgriScan.exe`).
   * Runs natively as a clean, windowed application with zero console clutter.

3. **Batch Launcher**:
   Double-click **`AgriScan.bat`** in this folder.

---

## ✨ Features & Architecture

* **Identical UI & Experience**:
  The user interface is 100% identical to the high-impact Harvest Voltage platform (Chamber, Calibration, Detection, and Report metrics with grain classification and distribution charts).
* **Native Desktop Window**:
  Runs in its own dedicated, native desktop frame (`1480x920`), auto-hiding menus, with high-contrast dark and archival light theme support and custom circular AgriScan branding.
* **Automated Background Management**:
  * Automatically spins up the Python FastAPI backend on port `8000` with the pre-trained Random Forest classifier.
  * **Zero orphan processes**: Closing the desktop app window automatically and cleanly terminates all background Python processes.
* **Persistent Uploads & Reports**:
  All uploaded images, calibrated photos, SQLite assessment ledger records, and certificates are saved locally.
