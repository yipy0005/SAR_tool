"""Install the Ketcher structure editor used by Find by structure.

Ketcher is EPAM Systems' open-source editor (https://github.com/epam/ketcher,
Apache-2.0). This script fetches one pinned standalone release, checks its
SHA-256 against the value below (GitHub's published digest for the asset),
and extracts only the files the editor page needs into vendor/ketcher/.
Nothing is sent anywhere: it is a single download from GitHub Releases, and
``--from-zip`` installs from a file you downloaded yourself.

Upgrading means changing KETCHER_VERSION and RELEASE_SHA256 together.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

KETCHER_VERSION = "3.18.0"
RELEASE_URL = f"https://github.com/epam/ketcher/releases/download/v{KETCHER_VERSION}/ketcher-standalone-{KETCHER_VERSION}.zip"
RELEASE_SHA256 = "484e7f10a0e74808ae5f43f0e6448db6f4d884bdd2189aa271108474167a3e7e"
SOURCE = "https://github.com/epam/ketcher"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "vendor" / "ketcher"
LICENSE_DIR = ROOT / "third_party" / "ketcher"  # LICENSE and NOTICE from the v3.18.0 tag
MANIFEST_NAME = "ketcher-install.json"
MAX_DOWNLOAD_BYTES = 80_000_000
MAX_EXTRACTED_BYTES = 120_000_000
# The release also ships popup / duo / closable demo builds (~30 MB each); only the main editor is kept.
_PAGE_FILES = {"index.html", "manifest.json", "favicon.ico", "favicon-16x16.png", "favicon-32x32.png", "apple-touch-icon.png", "logo.svg"}
_ASSET = re.compile(r"^static/(?:js/(?:main\.[0-9a-f]{6,}\.js|[0-9]+\.[0-9a-f]{6,}\.chunk\.js)(?:\.LICENSE\.txt)?|css/main\.[0-9a-f]{6,}\.css)$")


class KetcherInstallError(RuntimeError):
    """The release could not be verified or installed; nothing was changed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path, *, limit: int = MAX_DOWNLOAD_BYTES) -> None:
    """Stream ``url`` to ``destination``, refusing anything larger than ``limit`` bytes."""
    if not url.startswith("https://"):
        raise KetcherInstallError("Only HTTPS downloads are allowed.")
    received = 0
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as handle:  # noqa: S310 - pinned HTTPS URL
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            received += len(block)
            if received > limit:
                raise KetcherInstallError(f"The download exceeded {limit} bytes; stopped.")
            handle.write(block)


def wanted_member(name: str) -> bool:
    """Only the main editor page and its hashed assets; never paths that could leave the target."""
    if name.startswith(("/", "\\")) or "\\" in name or ".." in Path(name).parts:
        return False
    return name in _PAGE_FILES or bool(_ASSET.match(name))


def install_from_zip(archive: Path, target: Path = DEFAULT_TARGET, *, expected_sha256: str = RELEASE_SHA256, version: str = KETCHER_VERSION) -> dict:
    """Verify ``archive`` and replace ``target`` with the editor files. Raises before touching ``target`` on any problem."""
    actual = _sha256(archive)
    if actual != expected_sha256:
        raise KetcherInstallError(f"SHA-256 mismatch for {archive.name}: expected {expected_sha256}, got {actual}.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ketcher-", dir=target.parent))
    try:
        files = []
        total = 0
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or not wanted_member(member.filename):
                    continue
                destination = (staging / member.filename).resolve()
                if staging.resolve() not in destination.parents:
                    raise KetcherInstallError(f"Refusing archive entry outside the install folder: {member.filename}")
                total += member.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise KetcherInstallError("The archive expands to more than the allowed size.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, destination.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                files.append({"path": member.filename, "bytes": member.file_size})
        names = {item["path"] for item in files}
        if "index.html" not in names or not any(name.startswith("static/js/main.") and name.endswith(".js") for name in names):
            raise KetcherInstallError("The archive does not contain the Ketcher editor page (index.html and static/js/main.*.js).")
        for notice in ("LICENSE", "NOTICE"):
            if (LICENSE_DIR / notice).is_file():
                shutil.copyfile(LICENSE_DIR / notice, staging / notice)
        manifest = {
            "name": "Ketcher",
            "version": version,
            "source": SOURCE,
            "release_url": RELEASE_URL if version == KETCHER_VERSION else None,
            "release_sha256": expected_sha256,
            "license": "Apache-2.0",
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": sorted(files, key=lambda item: item["path"]),
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Install Ketcher {KETCHER_VERSION} (EPAM, Apache-2.0) for Find by structure.")
    parser.add_argument("--from-zip", type=Path, help=f"Install from a downloaded ketcher-standalone-{KETCHER_VERSION}.zip instead of downloading it")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Install folder (default: vendor/ketcher)")
    args = parser.parse_args(argv)
    try:
        if args.from_zip:
            manifest = install_from_zip(args.from_zip, args.target)
        else:
            with tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / f"ketcher-standalone-{KETCHER_VERSION}.zip"
                print(f"Downloading Ketcher {KETCHER_VERSION} from {RELEASE_URL} …")
                download(RELEASE_URL, archive)
                manifest = install_from_zip(archive, args.target)
    except (KetcherInstallError, OSError, zipfile.BadZipFile) as exc:
        print(f"Ketcher was not installed: {exc}", file=sys.stderr)
        return 1
    size = sum(item["bytes"] for item in manifest["files"]) / 1_000_000
    print(f"Installed Ketcher {manifest['version']} ({len(manifest['files'])} files, {size:.1f} MB) in {args.target}.")
    print("Restart the app; Find by structure will open Ketcher as its editor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
