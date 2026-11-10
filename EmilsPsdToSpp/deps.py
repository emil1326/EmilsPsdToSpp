"""Gets numpy into Painter's Python (the only package the plugin needs).

Painter ships its own Python without numpy, so the first time you use the
plugin it downloads the official numpy wheel from PyPI that matches Painter's
Python version, checks its SHA-256 and unzips it next to the plugin. Nothing is
installed anywhere else.
"""
import hashlib
import io
import json
import os
import platform
import re
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"


def _platform_tag():
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win_amd64" if machine in ("amd64", "x86_64") else "win_arm64"
    if sys.platform == "darwin":
        return "arm64" if machine == "arm64" else "x86_64"
    return "x86_64" if machine in ("amd64", "x86_64") else "aarch64"


DEPS_DIR = os.path.join(HERE, "_deps", f"{PY_TAG}-{_platform_tag()}")


def add_to_path():
    if os.path.isdir(DEPS_DIR) and DEPS_DIR not in sys.path:
        sys.path.insert(0, DEPS_DIR)


def have_numpy():
    add_to_path()
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def _wheel_ok(fname):
    if f"-{PY_TAG}-{PY_TAG}-" not in fname:
        return False
    plat = _platform_tag()
    if sys.platform == "win32":
        return fname.endswith(f"-{plat}.whl")
    if sys.platform == "darwin":
        return "macosx" in fname and (fname.endswith(f"_{plat}.whl") or "universal2" in fname)
    return "manylinux" in fname and fname.endswith(f"_{plat}.whl")


def _version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def find_wheel():
    with urllib.request.urlopen("https://pypi.org/pypi/numpy/json", timeout=60) as r:
        info = json.load(r)
    versions = [v for v in info["releases"] if re.fullmatch(r"\d+\.\d+\.\d+", v)]
    for v in sorted(versions, key=_version_key, reverse=True):
        for f in info["releases"][v]:
            if f["packagetype"] == "bdist_wheel" and not f.get("yanked") and _wheel_ok(f["filename"]):
                return v, f
    raise RuntimeError(f"no numpy wheel found for {PY_TAG} / {_platform_tag()}")


def install(progress=None):
    version, f = find_wheel()
    if progress:
        progress(f"Downloading numpy {version} ({f['size'] // 1_000_000} MB)...")
    with urllib.request.urlopen(f["url"], timeout=300) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != f["digests"]["sha256"]:
        raise RuntimeError("numpy download is corrupted (SHA-256 mismatch)")
    tmp = DEPS_DIR + ".tmp"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    if os.path.isdir(DEPS_DIR):
        import shutil
        shutil.rmtree(DEPS_DIR)
    os.replace(tmp, DEPS_DIR)
    add_to_path()
    return version, f["size"]


def wheel_size_hint():
    try:
        return find_wheel()[1]["size"]
    except Exception:
        return None
