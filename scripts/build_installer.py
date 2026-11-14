"""AgriScan Windows Installer Build Script (R-05).

Packages application components into an embedded payload ZIP, computes its
cryptographic SHA-256 checksum, embeds the hash into setup_form.cs, and
compiles the fail-closed AgriScanSetup.exe installer with csc.exe.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CSC_EXE = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

EXCLUDE_PATTERNS = [
    ".venv",
    "electron/dist",
    "electron/node_modules",
    "data/batches",
    "data/validation_run",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "agriscan.sqlite3",
    "electron.log",
]

# Sensitive patterns that must NEVER be packaged into the installer payload (Additional 1)
EXCLUDE_FILENAME_PATTERNS = [
    r"^\.env(\..+)?$",  # Matches .env, .env.local, .env.development, .env.production, .env.test, .env.*.local
    r".*\.(key|pem|pkcs12|pfx|cert|token)$",
    r"^\.auth_token$",
    r"^id_rsa.*$",
]


def is_excluded_path(rel_path: Path) -> bool:
    rel_str = str(rel_path).replace("\\", "/")
    filename = rel_path.name

    # Explicitly permit legitimate sample/template configuration files
    if filename in (".env.example", ".env.sample", ".env.template"):
        return False

    # Check directory and substring patterns
    if any(exc in rel_str for exc in EXCLUDE_PATTERNS):
        return True

    # Check sensitive filename patterns
    for pat in EXCLUDE_FILENAME_PATTERNS:
        if re.search(pat, filename, re.IGNORECASE):
            return True

    return False


def scan_file_for_secrets(file_path: Path) -> None:
    """Build-time safeguard to detect potential credentials in payload files."""
    if file_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".ico", ".joblib", ".exe", ".dll", ".zip"):
        return
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return
    if "-----BEGIN PRIVATE KEY-----" in content or "-----BEGIN RSA PRIVATE KEY-----" in content:
        raise ValueError(f"Build halted: Detected private key in payload file: {file_path}")


def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest().upper()


def build_installer(output_exe: Path | None = None) -> Path:
    out_target = output_exe or (ROOT_DIR / "AgriVisioSetup.exe")
    setup_cs = ROOT_DIR / "setup_form.cs"
    icon_ico = ROOT_DIR / "app_icon.ico"
    installer_manifest = ROOT_DIR / "installer.manifest"

    if not setup_cs.is_file():
        raise FileNotFoundError(f"Missing setup source: {setup_cs}")

    if not CSC_EXE.is_file():
        raise FileNotFoundError(f"Missing C# compiler at {CSC_EXE}")

    # Compile AgriVisio.exe and AgriScan.exe launcher with manifest and PE metadata
    launcher_cs = ROOT_DIR / "launcher.cs"
    launcher_manifest = ROOT_DIR / "launcher.manifest"
    launcher_exe_visio = ROOT_DIR / "AgriVisio.exe"
    launcher_exe_scan = ROOT_DIR / "AgriScan.exe"
    if launcher_cs.is_file() and launcher_manifest.is_file():
        launcher_cmd = [
            str(CSC_EXE),
            "/target:winexe",
            f"/out:{launcher_exe_visio}",
            f"/win32icon:{icon_ico}",
            f"/win32manifest:{launcher_manifest}",
            str(launcher_cs),
        ]
        res_l = subprocess.run(launcher_cmd, capture_output=True, text=True)
        if res_l.returncode != 0:
            print("Warning: Launcher compilation failed:", res_l.stderr)
        else:
            import shutil
            shutil.copy2(launcher_exe_visio, launcher_exe_scan)

    print("--- [1/4] Building installation payload ZIP ---")
    tmp_payload = ROOT_DIR / "agriscan_payload.zip"

    try:
        with zipfile.ZipFile(tmp_payload, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in [
                "AgriVisio.bat", "AgriScan.bat",
                "AgriVisio.exe", "AgriScan.exe",
                "app_icon.ico", "agrivisio_icon.ico", "LICENSE", "README.md", "requirements.txt",
            ]:
                fpath = ROOT_DIR / fname
                if fpath.is_file():
                    zf.write(fpath, arcname=fname)

            for dir_name in ["backend", "frontend", "electron"]:
                src_dir = ROOT_DIR / dir_name
                if not src_dir.is_dir():
                    continue
                for item in src_dir.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(ROOT_DIR)
                        rel_str = str(rel).replace("\\", "/")
                        if is_excluded_path(rel):
                            continue
                        scan_file_for_secrets(item)
                        zf.write(item, arcname=str(rel))

        payload_size_mb = tmp_payload.stat().st_size / (1024 * 1024)
        print(f"Payload created: {payload_size_mb:.2f} MB")

        print("--- [2/4] Computing SHA-256 checksum of exact payload ---")
        payload_sha256 = compute_sha256(tmp_payload)
        print(f"Computed Payload SHA-256: {payload_sha256}")

        print("--- [3/4] Embedding SHA-256 into setup_form.cs ---")
        cs_code = setup_cs.read_text(encoding="utf-8")
        cs_code_updated = re.sub(
            r'public static string ExpectedPayloadSha256\s*=\s*"[^"]*";',
            f'public static string ExpectedPayloadSha256 = "{payload_sha256}";',
            cs_code,
        )
        setup_cs.write_text(cs_code_updated, encoding="utf-8")

        print(f"--- [4/4] Compiling {out_target.name} with csc.exe ---")
        compile_cmd = [
            str(CSC_EXE),
            "/target:winexe",
            f"/out:{out_target}",
            f"/win32icon:{icon_ico}",
            f"/win32manifest:{installer_manifest}",
            "/reference:System.IO.Compression.dll",
            "/reference:System.IO.Compression.FileSystem.dll",
            f"/resource:{tmp_payload},agriscan_payload.zip",
            str(setup_cs),
        ]

        res = subprocess.run(compile_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print("Compilation STDERR:\n", res.stderr)
            print("Compilation STDOUT:\n", res.stdout)
            raise RuntimeError(f"C# compilation failed with code {res.returncode}")

        print(f"Successfully compiled {out_target} ({out_target.stat().st_size / 1024:.1f} KB)")
        print(f"Embedded payload SHA-256: {payload_sha256}")

        if out_target.name == "AgriVisioSetup.exe":
            import shutil
            shutil.copy2(out_target, ROOT_DIR / "AgriScanSetup.exe")

        return out_target
    finally:
        if tmp_payload.is_file():
            try:
                tmp_payload.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    build_installer()
