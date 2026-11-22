"""AgriVisio Windows Installer Build Script (R-05).

Packages application components into an embedded payload ZIP, computes its
cryptographic SHA-256 checksum, embeds the hash into setup_form.cs, and
compiles the fail-closed AgriVisioSetup.exe installer with csc.exe.
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
    "agrivisio.sqlite3",
    "electron.log",
]

# Sensitive patterns that must NEVER be packaged into the installer payload (Additional 1)
EXCLUDE_FILENAME_PATTERNS = [
    r"^\.env(\..+)?$",  # Matches .env, .env.local, .env.development, .env.production, .env.test, .env.*.local
    r".*\.(key|pem|pkcs12|pfx|cert|token)$",
    r"^\.auth_token$",
    r"^id_rsa.*$",
]


def is_excluded_path(rel_path: Path, bundle_runtime: bool = False) -> bool:
    rel_str = str(rel_path).replace("\\", "/")
    filename = rel_path.name

    # Explicitly permit legitimate sample/template configuration files
    if filename in (".env.example", ".env.sample", ".env.template"):
        return False

    excludes = list(EXCLUDE_PATTERNS)
    if bundle_runtime:
        excludes = [x for x in excludes if x != "electron/dist"]

    # Check directory and substring patterns
    if any(exc in rel_str for exc in excludes):
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


REQUIRED_ELECTRON_FILES = ("electron.exe", "icudtl.dat", "resources.pak", "v8_context_snapshot.bin")
REQUIRED_PYTHON_FILES = ("python.exe", "python3.dll", f"python3{sys.version_info.minor}.dll")
REQUIRED_PYTHON_DIRS = ("Lib",)


def is_complete_electron_dist(dist_dir: Path | None) -> bool:
    if dist_dir is None or not dist_dir.is_dir():
        return False
    return all((dist_dir / req).is_file() for req in REQUIRED_ELECTRON_FILES)


def is_complete_python_dist(dist_dir: Path | None) -> bool:
    if dist_dir is None or not dist_dir.is_dir():
        return False
    has_files = all((dist_dir / req).is_file() for req in REQUIRED_PYTHON_FILES)
    has_dirs = all((dist_dir / req).is_dir() for req in REQUIRED_PYTHON_DIRS)
    return has_files and has_dirs


def build_payload_zip(tmp_payload: Path, bundle_runtime: bool = False) -> Path:
    """Build the installation payload ZIP archive with strict exclusion and runtime validation."""
    local_app_data_env = os.getenv("LOCALAPPDATA")
    local_app_data = Path(local_app_data_env) / "Programs" / "AgriVisio" if local_app_data_env and os.path.isabs(local_app_data_env) else None
    installed_python = (local_app_data / "backend" / "python") if local_app_data else None
    installed_electron = (local_app_data / "electron" / "dist") if local_app_data else None

    python_source_dir = None
    if bundle_runtime:
        repo_python_dir = ROOT_DIR / "backend" / "python"
        if is_complete_python_dist(repo_python_dir):
            python_source_dir = repo_python_dir
        elif is_complete_python_dist(installed_python):
            python_source_dir = installed_python
        else:
            raise RuntimeError(
                "Runtime bundling requested (--bundle-runtime) but neither repository "
                "nor installed Python distribution is complete (must include python.exe, python3.dll, versioned core DLL, and Lib/)."
            )

    electron_dist_source_dir = None
    if bundle_runtime:
        repo_electron_dir = ROOT_DIR / "electron" / "dist"
        if is_complete_electron_dist(repo_electron_dir):
            electron_dist_source_dir = repo_electron_dir
        elif is_complete_electron_dist(installed_electron):
            electron_dist_source_dir = installed_electron
        else:
            raise RuntimeError(
                "Runtime bundling requested (--bundle-runtime) but neither repository "
                "nor installed Electron distribution is complete (must include electron.exe, icudtl.dat, resources.pak, v8_context_snapshot.bin)."
            )

    with zipfile.ZipFile(tmp_payload, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in [
            "AgriVisio.bat",
            "AgriVisio.exe",
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

                    # Exclude runtime folders from generic traversal; handled exclusively below
                    if rel_str.startswith("backend/python/") or rel_str == "backend/python":
                        continue
                    if rel_str.startswith("electron/dist/") or rel_str == "electron/dist":
                        continue

                    if is_excluded_path(rel, bundle_runtime=bundle_runtime):
                        continue
                    scan_file_for_secrets(item)
                    zf.write(item, arcname=str(rel))

        if python_source_dir is not None:
            print(f"Bundling portable Python runtime from {python_source_dir}...")
            for py_item in python_source_dir.rglob("*"):
                if py_item.is_file():
                    rel_py = py_item.relative_to(python_source_dir)
                    if is_excluded_path(Path("backend/python") / rel_py, bundle_runtime=True):
                        continue
                    scan_file_for_secrets(py_item)
                    arc = "backend/python/" + str(rel_py).replace("\\", "/")
                    zf.write(py_item, arcname=arc)

        if electron_dist_source_dir is not None:
            print(f"Bundling portable Electron runtime from {electron_dist_source_dir}...")
            for el_item in electron_dist_source_dir.rglob("*"):
                if el_item.is_file():
                    rel_el = el_item.relative_to(electron_dist_source_dir)
                    if is_excluded_path(Path("electron/dist") / rel_el, bundle_runtime=True):
                        continue
                    scan_file_for_secrets(el_item)
                    arc = "electron/dist/" + str(rel_el).replace("\\", "/")
                    zf.write(el_item, arcname=arc)

    return tmp_payload


def build_installer(output_exe: Path | None = None) -> Path:
    out_target = output_exe or (ROOT_DIR / "AgriVisioSetup.exe")
    setup_cs = ROOT_DIR / "setup_form.cs"
    icon_ico = ROOT_DIR / "agrivisio_icon.ico" if (ROOT_DIR / "agrivisio_icon.ico").is_file() else (ROOT_DIR / "app_icon.ico")
    installer_manifest = ROOT_DIR / "installer.manifest"

    if not setup_cs.is_file():
        raise FileNotFoundError(f"Missing setup source: {setup_cs}")

    if not CSC_EXE.is_file():
        raise FileNotFoundError(f"Missing C# compiler at {CSC_EXE}")

    # Compile AgriVisio.exe launcher with manifest and PE metadata
    launcher_cs = ROOT_DIR / "launcher.cs"
    launcher_manifest = ROOT_DIR / "launcher.manifest"
    launcher_exe_visio = ROOT_DIR / "AgriVisio.exe"
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

    bundle_runtime = (
        "--bundle-runtime" in sys.argv
        or os.getenv("AGRIVISIO_BUNDLE_RUNTIME", "0").lower() in ("1", "true", "yes")
    )

    print("--- [1/4] Building installation payload ZIP ---")
    tmp_payload = ROOT_DIR / "agrivisio_payload.zip"

    try:
        build_payload_zip(tmp_payload, bundle_runtime=bundle_runtime)
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
            "/reference:System.Management.dll",
            f"/resource:{tmp_payload},agrivisio_payload.zip",
            f"/resource:{ROOT_DIR / 'frontend' / 'assets' / 'emblem.png'},app_emblem.png",
            str(setup_cs),
        ]

        res = subprocess.run(compile_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print("Compilation STDERR:\n", res.stderr)
            print("Compilation STDOUT:\n", res.stdout)
            raise RuntimeError(f"C# compilation failed with code {res.returncode}")

        print(f"Successfully compiled {out_target} ({out_target.stat().st_size / 1024:.1f} KB)")
        print(f"Embedded payload SHA-256: {payload_sha256}")

        return out_target
    finally:
        if tmp_payload.is_file():
            try:
                tmp_payload.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    build_installer()
