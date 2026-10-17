"""Materialize trusted installed package assets before strict source validation.

Some installers (including uv) hardlink wheel resources into site-packages.
Only this module's fixed package-owned asset directory may use that storage
layout. Arbitrary imported source manifests still reject all hardlinks.
"""
from __future__ import annotations
from pathlib import Path
import shutil
import stat
import tempfile
import threading
from .common import ForgeError

_CACHE = None
_LOCK = threading.Lock()


def _copy_trusted_package_assets(source, destination):
    """Internal package resource copy, not a general untrusted-source importer."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise ForgeError("Installed package assets must be a real directory")
    total, count = 0, 0
    for item in sorted(source.rglob("*")):
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ForgeError("Symlink in installed package resources")
        target = destination / item.relative_to(source)
        if stat.S_ISDIR(mode):
            target.mkdir(parents=True, exist_ok=True)
        elif stat.S_ISREG(mode):
            count += 1
            total += item.stat().st_size
            # The complete paper playbooks add official task, technology and
            # work-context tables; their verified raw bundle is about 50 MB.
            # Keep the installed-resource bound separate from download limits.
            if count > 128 or total > 64_000_000:
                raise ForgeError("Installed anchor bundle exceeds expected resource bounds")
            target.parent.mkdir(parents=True, exist_ok=True)
            # copyfile creates a private inode even when the installer used hardlinks.
            shutil.copyfile(item, target)
            target.chmod(0o400)
            if target.stat().st_nlink != 1:
                raise ForgeError("Could not materialize a private package resource copy")
        else:
            raise ForgeError("Special file in installed package resources")
    from .sources import load_source_manifest
    for name in ("usgs_manifest.json", "onet_manifest.json"):
        load_source_manifest(destination / name)
    return destination


def packaged_anchor_root():
    """Return verified private copies; keep them alive for this Python process."""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            temporary = tempfile.TemporaryDirectory(prefix="wgforge-package-assets-")
            try:
                _copy_trusted_package_assets(Path(__file__).resolve().parent / "data" / "anchors", Path(temporary.name))
            except BaseException:
                temporary.cleanup()
                raise
            _CACHE = temporary
        return Path(_CACHE.name)


def bundled_anchor_root():
    project = Path(__file__).resolve().parent.parent / "examples" / "anchors"
    if (project / "onet_curated_smoke.json").is_file():
        return project
    return packaged_anchor_root()
