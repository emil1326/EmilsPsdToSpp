"""Dev only: compare EmilsPsdToSpp/psd_reader.py against psd-tools.

Usage: python -X utf8 dev/compare_psd_reader.py [psd files or a folder]
Needs psd-tools and numpy. Exits non-zero on any mismatch.
"""

import glob
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "EmilsPsdToSpp"))

import psd_reader  # noqa: E402
from psd_tools import PSDImage  # noqa: E402

DEFAULT_DIR = os.environ.get("EMILS_PSD_TEST_DIR", "")  # folder of PSDs to check

stats = {"layers": 0, "pixels": 0, "masks": 0, "mismatches": 0}
t_mine = 0.0
errors = []


def bad(path, msg):
    stats["mismatches"] += 1
    errors.append("%s: %s" % (path, msg))


def compare_layer(mine, ref, path):
    global t_mine
    stats["layers"] += 1
    for attr, theirs in (
        ("name", ref.name),
        ("visible", ref.visible),
        ("opacity", ref.opacity),
        ("blend", ref.blend_mode.name),
        ("clipping", ref.clipping),
        ("is_group", ref.is_group()),
    ):
        if getattr(mine, attr) != theirs:
            bad(path, "%s: mine=%r psd-tools=%r" % (attr, getattr(mine, attr), theirs))

    if ref.is_group():
        compare_list(mine.children, list(ref), path)
        return

    if (mine.left, mine.top, mine.right, mine.bottom) != (ref.left, ref.top, ref.right, ref.bottom):
        bad(path, "bbox mine=%r psd-tools=%r" % (
            (mine.left, mine.top, mine.right, mine.bottom),
            (ref.left, ref.top, ref.right, ref.bottom)))

    # pixels
    t = time.perf_counter()
    got = mine.pixels()
    t_mine += time.perf_counter() - t
    img = ref.topil()
    if img is None:
        if got is not None:
            bad(path, "pixels: mine has data, psd-tools None")
    elif got is None:
        bad(path, "pixels: mine None, psd-tools has data")
    else:
        stats["pixels"] += 1
        arr, left, top = got
        exp = np.array(img.convert("RGBA") if img.mode != "RGBA" else img)
        if img.mode in ("RGB", "L"):
            exp[:, :, 3] = 255
        if (left, top) != (ref.left, ref.top):
            bad(path, "pixels origin mine=%r psd-tools=%r" % ((left, top), (ref.left, ref.top)))
        elif arr.shape != exp.shape or not np.array_equal(arr, exp):
            diff = int((arr != exp).sum()) if arr.shape == exp.shape else -1
            bad(path, "pixels differ (shape %s vs %s, %d bytes differ)" % (arr.shape, exp.shape, diff))

    # mask
    t = time.perf_counter()
    m = mine.mask()
    t_mine += time.perf_counter() - t
    rm = ref.mask
    rimg = rm.topil() if rm is not None else None
    if rimg is None:
        if m is not None:
            bad(path, "mask: mine has data, psd-tools None")
    elif m is None:
        bad(path, "mask: mine None, psd-tools has data")
    else:
        stats["masks"] += 1
        exp = np.array(rimg)
        if exp.ndim == 3:
            exp = exp[:, :, 0]
        if (m["left"], m["top"]) != (rm.left, rm.top):
            bad(path, "mask origin mine=%r psd-tools=%r" % ((m["left"], m["top"]), (rm.left, rm.top)))
        if m["background"] != rm.background_color:
            bad(path, "mask background mine=%r psd-tools=%r" % (m["background"], rm.background_color))
        if m["disabled"] != rm.disabled:
            bad(path, "mask disabled mine=%r psd-tools=%r" % (m["disabled"], rm.disabled))
        if m["data"].shape != exp.shape or not np.array_equal(m["data"], exp):
            bad(path, "mask data differs (shape %s vs %s)" % (m["data"].shape, exp.shape))


def compare_list(mine, ref, path):
    if len(mine) != len(ref):
        bad(path, "child count mine=%d psd-tools=%d" % (len(mine), len(ref)))
        return
    for a, b in zip(mine, ref):
        compare_layer(a, b, path + "/" + b.name)


def main():
    args = sys.argv[1:] or [DEFAULT_DIR]
    files = []
    for a in args:
        files += sorted(glob.glob(os.path.join(a, "*.psd"))) if os.path.isdir(a) else [a]
    if not files:
        print("no PSD files found")
        return 1

    t_read = 0.0
    for f in files:
        t = time.perf_counter()
        psd = psd_reader.read_psd(f)
        t_read += time.perf_counter() - t
        ref = PSDImage.open(f)
        before = stats["mismatches"]
        for attr, theirs in (("width", ref.width), ("height", ref.height), ("depth", ref.depth)):
            if getattr(psd, attr) != theirs:
                bad(os.path.basename(f), "%s mismatch" % attr)
        compare_list(psd.layers, list(ref), os.path.basename(f))
        print("%-40s %s" % (os.path.basename(f),
                            "ok" if stats["mismatches"] == before else "MISMATCH"))

    print()
    for e in errors[:50]:
        print("  " + e)
    print("files %d, layers checked %d (pixel layers %d, masks %d), mismatches %d"
          % (len(files), stats["layers"], stats["pixels"], stats["masks"], stats["mismatches"]))
    print("my reader: parse %.2fs + pixels()/mask() %.2fs" % (t_read, t_mine))
    return 1 if stats["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
