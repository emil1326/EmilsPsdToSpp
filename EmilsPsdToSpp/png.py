"""Minimal PNG writer (grayscale or RGB, 8-bit), so we don't need Pillow."""
import struct
import zlib

import numpy as np


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def write_png(path, arr, level=1):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    color = 0 if arr.ndim == 2 else {3: 2, 4: 6}[arr.shape[2]]
    rows = arr.reshape(h, -1)
    raw = np.hstack([np.zeros((h, 1), np.uint8), rows]).tobytes()  # filter 0 on each row
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0)))
        f.write(_chunk(b"IDAT", zlib.compress(raw, level)))
        f.write(_chunk(b"IEND", b""))
