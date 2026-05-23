"""Resolve the rtk binary path.

Strategy:
  1. Prefer a user-installed rtk in PATH (brew, cargo, package manager).
  2. Otherwise download the latest release from github.com/rtk-ai/rtk
     into ~/.burnless/bin/v<VERSION>/. Latest tag is cached for 24h so
     we don't hit GitHub on every invocation; the binary itself is
     permanent-cached per version.
  3. If neither works (unsupported platform, offline + no cache), raise
     with install instructions.

Set RTK_VERSION to a concrete tag (e.g. "0.41.0") instead of "latest" to
pin — useful for reproducible CI or when a release introduces a regression.

RTK is Apache-2.0 licensed and ships pre-built binaries per platform —
no toolchain required on the user side.
"""
from __future__ import annotations

import json
import platform
import shutil
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

# "latest" → fetch newest tag from GitHub (24h cache). Pin to a string like
# "0.41.0" to freeze. Last known working version if the API is unreachable:
RTK_VERSION = "latest"
RTK_FALLBACK_VERSION = "0.41.0"
LATEST_CACHE_TTL_SECONDS = 24 * 3600
LATEST_API_URL = "https://api.github.com/repos/rtk-ai/rtk/releases/latest"

# (system, machine) → (release asset filename, archive type)
RTK_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("Darwin",  "arm64"):   ("rtk-aarch64-apple-darwin.tar.gz",      "tar.gz"),
    ("Darwin",  "x86_64"):  ("rtk-x86_64-apple-darwin.tar.gz",       "tar.gz"),
    ("Linux",   "x86_64"):  ("rtk-x86_64-unknown-linux-musl.tar.gz", "tar.gz"),
    ("Linux",   "aarch64"): ("rtk-aarch64-unknown-linux-gnu.tar.gz", "tar.gz"),
    ("Windows", "AMD64"):   ("rtk-x86_64-pc-windows-msvc.zip",       "zip"),
}

class RTKNotAvailable(RuntimeError):
    pass


def resolve_rtk() -> str:
    """Return an absolute path to a working rtk binary. Downloads + caches if needed."""
    in_path = shutil.which("rtk")
    if in_path:
        return in_path
    version = resolve_version()
    cached = _cached_binary_path(version)
    if cached.exists():
        return str(cached)
    return _download_and_cache(cached, version)


def resolve_version() -> str:
    """Return the concrete version tag to use. Honors a pinned RTK_VERSION;
    when 'latest', queries the GitHub releases API at most once per 24h."""
    if RTK_VERSION != "latest":
        return RTK_VERSION
    cache = Path.home() / ".burnless" / "bin" / ".latest-version.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < LATEST_CACHE_TTL_SECONDS:
        try:
            return json.loads(cache.read_text())["version"]
        except (json.JSONDecodeError, KeyError):
            pass
    try:
        with urllib.request.urlopen(LATEST_API_URL, timeout=10) as r:
            data = json.loads(r.read().decode())
        version = data["tag_name"].lstrip("v")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"version": version, "checked_at": time.time()}))
        return version
    except Exception:
        # Offline or rate-limited — fall back to a known-good pin.
        return RTK_FALLBACK_VERSION


def _cached_binary_path(version: str) -> Path:
    name = "rtk.exe" if platform.system() == "Windows" else "rtk"
    return Path.home() / ".burnless" / "bin" / f"v{version}" / name


def _download_and_cache(target: Path, version: str) -> str:
    key = (platform.system(), platform.machine())
    asset = RTK_ASSETS.get(key)
    if not asset:
        release_base = f"https://github.com/rtk-ai/rtk/releases/download/v{version}"
        raise RTKNotAvailable(
            f"No rtk pre-built binary for {key}. "
            f"Install manually: `brew install rtk`, `cargo install rtk`, "
            f"or download from {release_base}."
        )
    asset_name, archive_type = asset
    release_base = f"https://github.com/rtk-ai/rtk/releases/download/v{version}"
    url = f"{release_base}/{asset_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.parent / asset_name
    print(f"burnless: fetching rtk v{version} for {key[0]}/{key[1]}...")
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
