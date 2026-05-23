"""Resolve the rtk binary path.

Strategy:
  1. Prefer a user-installed rtk in PATH (brew, cargo, package manager).
  2. Otherwise download a pinned version from github.com/rtk-ai/rtk/releases
     into ~/.burnless/bin/v<VERSION>/. Cached after first download.
  3. If neither works (unsupported platform, no network), raise with
     install instructions.

RTK is Apache-2.0 licensed and ships pre-built binaries per platform —
no toolchain required on the user side.
"""
from __future__ import annotations

import platform
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

RTK_VERSION = "0.41.0"

# (system, machine) → (release asset filename, archive type)
RTK_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("Darwin",  "arm64"):   ("rtk-aarch64-apple-darwin.tar.gz",      "tar.gz"),
    ("Darwin",  "x86_64"):  ("rtk-x86_64-apple-darwin.tar.gz",       "tar.gz"),
    ("Linux",   "x86_64"):  ("rtk-x86_64-unknown-linux-musl.tar.gz", "tar.gz"),
    ("Linux",   "aarch64"): ("rtk-aarch64-unknown-linux-gnu.tar.gz", "tar.gz"),
    ("Windows", "AMD64"):   ("rtk-x86_64-pc-windows-msvc.zip",       "zip"),
}

RTK_RELEASE_BASE = f"https://github.com/rtk-ai/rtk/releases/download/v{RTK_VERSION}"


class RTKNotAvailable(RuntimeError):
    pass


def resolve_rtk() -> str:
    """Return an absolute path to a working rtk binary. Downloads + caches if needed."""
    in_path = shutil.which("rtk")
    if in_path:
        return in_path
    cached = _cached_binary_path()
    if cached.exists():
        return str(cached)
    return _download_and_cache(cached)


def _cached_binary_path() -> Path:
    name = "rtk.exe" if platform.system() == "Windows" else "rtk"
    return Path.home() / ".burnless" / "bin" / f"v{RTK_VERSION}" / name


def _download_and_cache(target: Path) -> str:
    key = (platform.system(), platform.machine())
    asset = RTK_ASSETS.get(key)
    if not asset:
        raise RTKNotAvailable(
            f"No rtk pre-built binary for {key}. "
            f"Install manually: `brew install rtk`, `cargo install rtk`, "
            f"or download from {RTK_RELEASE_BASE}."
        )
    asset_name, archive_type = asset
    url = f"{RTK_RELEASE_BASE}/{asset_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / asset_name
    print(f"burnless: fetching rtk v{RTK_VERSION} for {key[0]}/{key[1]}...")
    urllib.request.urlretrieve(url, archive)
    bin_name = "rtk.exe" if platform.system() == "Windows" else "rtk"
    extracted = _extract_binary(archive, archive_type, target.parent, bin_name)
    if extracted != target:
        extracted.rename(target)
    archive.unlink(missing_ok=True)
    target.chmod(0o755)
    return str(target)


def _extract_binary(archive: Path, archive_type: str, dest_dir: Path, bin_name: str) -> Path:
    if archive_type == "tar.gz":
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if Path(m.name).name == bin_name:
                    t.extract(m, dest_dir)
                    return dest_dir / m.name
    elif archive_type == "zip":
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if Path(n).name == bin_name:
                    z.extract(n, dest_dir)
                    return dest_dir / n
    raise RTKNotAvailable(f"rtk binary not found inside {archive}")
