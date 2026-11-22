"""The Liquid Glass pixel kernel (glass.c), compiled on first use and loaded with ctypes.

Per-pixel refraction over a whole panel is ~700k pixels per frame: seconds in
Python, a few milliseconds in C. The C file has no dependencies beyond libc and
libm, so all it needs is a C compiler (`cc`). It is built into
~/.cache/cornerwindow/glass-<hash>.so, so editing glass.c rebuilds it.
`install.sh` builds it ahead of time. Without a compiler, `available()` is False
and the glass theme hides itself.

ctypes releases the GIL while the kernel runs.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

from ..core import log

_log = log.get("native")
SOURCE = Path(__file__).with_name("glass.c")
ABI = 4

_lock = threading.Lock()
_lib: ctypes.CDLL | None = None
_error: str | None = None
_tried = False


class Seg(ctypes.Structure):
    _fields_ = [(name, ctypes.c_float) for name in ("x0", "y0", "x1", "y1", "n0x", "n0y", "n1x", "n1y")]


_f, _i, _p = ctypes.c_float, ctypes.c_int, ctypes.c_void_p


class Params(ctypes.Structure):
    _fields_ = [
        ("w", _i), ("h", _i), ("out_stride", _i),
        ("src", _p), ("src_w", _i), ("src_h", _i), ("src_stride", _i),
        ("src_ox", _f), ("src_oy", _f), ("src_step", _f),
        ("lo", _p), ("lo_w", _i), ("lo_h", _i), ("lo_stride", _i), ("lo_scale", _f), ("lo_x", _f), ("lo_y", _f),
        ("mask", _p), ("mask_stride", _i),
        ("dist", _p), ("nx", _p), ("ny", _p),
        ("bezel", _f), ("disp", _f), ("dispersion", _f), ("frost", _f),
        ("saturation", _f), ("brightness", _f),
        ("tint_r", _f), ("tint_g", _f), ("tint_b", _f), ("tint_a", _f),
        ("light_x", _f), ("light_y", _f),
        ("rim", _f), ("rim_ambient", _f), ("rim_opposite", _f), ("rim_glow", _f),
        ("shade", _f),
        ("press_x", _f), ("press_y", _f), ("press_r", _f), ("press_a", _f),
        ("skip_interior", _i),
    ]


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "cornerwindow"


def library_path() -> Path:
    digest = hashlib.sha1(SOURCE.read_bytes()).hexdigest()[:12]
    return cache_dir() / f"glass-{digest}.so"


def compiler() -> str | None:
    for name in (os.environ.get("CC") or "", "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return name
    return None


def build(force: bool = False) -> Path:
    """Compile glass.c if needed; returns the .so path or raises RuntimeError."""
    target = library_path()
    if target.exists() and not force:
        return target
    cc = compiler()
    if cc is None:
        raise RuntimeError("no C compiler found (install gcc or clang)")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    base = [cc, "-O3", "-ffast-math", "-fPIC", "-shared", "-std=c99", str(SOURCE), "-o", str(tmp), "-lm"]
    last = ""
    for extra in (["-march=native"], []):
        result = subprocess.run(base[:1] + extra + base[1:], capture_output=True, text=True)
        if result.returncode == 0:
            tmp.replace(target)
            for old in target.parent.glob("glass-*.so"):  # drop builds of older sources
                if old != target:
                    old.unlink(missing_ok=True)
            _log.info("Built glass kernel %s", target.name)
            return target
        last = (result.stderr or result.stdout).strip()
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"compiling glass.c failed: {last[:400]}")


def load() -> ctypes.CDLL | None:
    """The kernel, building it on first use. None (and `error()` says why) if unavailable."""
    global _lib, _error, _tried
    with _lock:
        if _tried:
            return _lib
        _tried = True
        try:
            lib = ctypes.CDLL(str(build()))
            if lib.cw_abi_version() != ABI:
                lib = ctypes.CDLL(str(build(force=True)))
            lib.cw_edge_field.argtypes = [ctypes.POINTER(Seg), _i, _i, _i, _f, _p, _p, _p]
            lib.cw_edge_field.restype = None
            lib.cw_prepare_backdrop.argtypes = [_p, _i, _i, _i, _i, _i, _p, _i, _i, _i]
            lib.cw_prepare_backdrop.restype = _i
            lib.cw_shade.argtypes = [ctypes.POINTER(Params), _p]
            lib.cw_shade.restype = None
            lib.cw_shade_rows.argtypes = [ctypes.POINTER(Params), _p, _i, _i, _i]
            lib.cw_shade_rows.restype = None
            lib.cw_tint_lo.argtypes = [ctypes.POINTER(Params), _p]
            lib.cw_tint_lo.restype = None
            lib.cw_luma.argtypes = [_p, _i, _i, _i]
            lib.cw_luma.restype = ctypes.c_double
            _lib = lib
        except Exception as exc:
            _error = str(exc)
            _log.warning("Liquid Glass unavailable: %s", exc)
        return _lib


def available() -> bool:
    return load() is not None


def error() -> str | None:
    load()
    return _error


def address(buffer) -> int:
    """Address of a writable buffer (bytearray, cairo surface data memoryview, ctypes array)."""
    if isinstance(buffer, int):
        return buffer
    if isinstance(buffer, ctypes.Array):
        return ctypes.addressof(buffer)
    return ctypes.addressof((ctypes.c_char * len(memoryview(buffer).cast("B"))).from_buffer(buffer))
