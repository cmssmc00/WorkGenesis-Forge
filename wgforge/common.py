"""Small dependency-free integrity and filesystem primitives."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from datetime import datetime, timezone

class ForgeError(ValueError):
    """Actionable, safe error suitable for the CLI."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            result.update(chunk)
    return result.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda s: (_ for _ in ()).throw(ForgeError("Non-finite JSON is forbidden")))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_path(root, name, must_exist=False):
    root = Path(root).absolute()
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ForgeError("Invalid relative path")
    parts = PurePosixPath(name).parts
    if PurePosixPath(name).is_absolute() or any(p in {".", "..", ""} for p in name.split("/")):
        raise ForgeError("Absolute paths, traversal, and empty path segments are forbidden")
    target = root.joinpath(*parts)
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ForgeError("Symlinks are forbidden")
        if path == root:
            break
    if not target.resolve().is_relative_to(root.resolve()):
        raise ForgeError("Path escapes allowed directory")
    if must_exist and not target.is_file():
        raise ForgeError("Required file is missing: " + name)
    return target


def tree_hashes(root):
    root = Path(root)
    if root.is_symlink():
        raise ForgeError("Symlinks are forbidden")
    return {p.relative_to(root).as_posix(): sha256(safe_path(root, p.relative_to(root).as_posix(), True))
            for p in sorted(root.rglob("*")) if p.is_file() or p.is_symlink()}


def require_id(value, label="identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value):
        raise ForgeError("Invalid " + label)
    return value


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ForgeError(label + " must be a finite number")
    return value
