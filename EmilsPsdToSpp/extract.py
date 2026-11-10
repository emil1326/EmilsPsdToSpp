"""Step 1: PSD -> layer tree of flat colors + full-size mask PNGs.

Works outside Painter too (only needs numpy), which makes it easy to test.
"""
import os
import re

import numpy as np

from . import names
from .fit import fit_colors, mul
from .png import write_png
from .psd_reader import read_psd

# psd blend mode name -> Painter BlendingMode name
BLEND = {
    "NORMAL": "Normal", "MULTIPLY": "Multiply", "SCREEN": "Screen",
    "OVERLAY": "Overlay", "DARKEN": "Darken", "LIGHTEN": "Lighten",
    "LINEAR_DODGE": "LinearDodge", "LINEAR_BURN": "LinearBurn",
    "COLOR_DODGE": "ColorDodge", "COLOR_BURN": "ColorBurn",
    "SOFT_LIGHT": "SoftLight", "HARD_LIGHT": "HardLight",
    "VIVID_LIGHT": "VividLight", "LINEAR_LIGHT": "LinearLight",
    "PIN_LIGHT": "PinLight", "DIFFERENCE": "Difference",
    "EXCLUSION": "Exclusion", "SUBTRACT": "Subtract", "DIVIDE": "Divide",
    "HUE": "Tint", "SATURATION": "Saturation", "COLOR": "Color",
    "LUMINOSITY": "Value", "PASS_THROUGH": "PassThrough",
}


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_") or "layer"


class _Extractor:
    def __init__(self, psd, out_dir, translate, skip, tolerance, progress):
        self.psd, self.out = psd, out_dir
        self.translate, self.skip = translate, skip
        self.tolerance, self.progress = tolerance, progress
        self.counter = 0
        self.dropped, self.empty = [], []

    def save_mask(self, arr, stem):
        if (arr == 255).all():
            return None
        name = f"{stem}.png"
        write_png(os.path.join(self.out, name), arr)
        return name

    def layer(self, layer, stem, blend, opacity):
        got = layer.pixels()
        if got is None:
            return None
        rgba, left, top = got
        w, h = self.psd.width, self.psd.height
        # crop to the canvas
        x0, y0 = max(left, 0), max(top, 0)
        x1, y1 = min(left + rgba.shape[1], w), min(top + rgba.shape[0], h)
        if x1 <= x0 or y1 <= y0:
            return None
        rgba = rgba[y0 - top:y1 - top, x0 - left:x1 - left]
        alpha = np.zeros((h, w), np.uint8)
        alpha[y0:y1, x0:x1] = rgba[..., 3]
        rgb = np.zeros((h, w, 3), np.uint8)
        rgb[y0:y1, x0:x1] = rgba[..., :3]

        m = layer.mask()
        if m is not None and not m["disabled"]:
            full = np.full((h, w), m["background"], np.uint8)
            d, ml, mt = m["data"], m["left"], m["top"]
            mx0, my0 = max(ml, 0), max(mt, 0)
            mx1, my1 = min(ml + d.shape[1], w), min(mt + d.shape[0], h)
            if mx1 > mx0 and my1 > my0:
                full[my0:my1, mx0:mx1] = d[my0 - mt:my1 - mt, mx0 - ml:mx1 - ml]
            alpha = (alpha.astype(np.uint16) * full // 255).astype(np.uint8)
        if not alpha.any():
            return None

        spec, t = fit_colors(rgb, alpha, blend, opacity, self.tolerance)
        out = {"fill": spec}
        if spec["mode"] == "tint":
            out["mask"] = self.save_mask(mul(alpha, t), stem + "_mask")
        else:
            out["mask"] = self.save_mask(alpha, stem + "_mask")
        if spec["mode"] == "two":
            out["mask2"] = self.save_mask(mul(alpha, t), stem + "_mask2")
        if spec["mode"] == "bitmap":
            out["bitmap"] = stem + "_color.png"
            write_png(os.path.join(self.out, out["bitmap"]), rgb)
        return out

    def walk(self, layers, path):
        nodes = []
        for layer in layers:  # bottom -> top
            raw = layer.name
            name = names.translate(raw) if self.translate else raw.replace("\x00", "").strip()
            full = "/".join(path + [name])
            if self.skip(raw, name):
                self.dropped.append(full)
                continue
            node = {
                "name": name,
                "visible": bool(layer.visible),
                "opacity": round(layer.opacity / 255.0, 4),
                "blend": BLEND.get(layer.blend, "Normal"),
            }
            if layer.is_group:
                node["type"] = "group"
                node["children"] = self.walk(layer.children, path + [name])
                if not node["children"]:
                    continue
            else:
                self.counter += 1
                if self.progress:
                    self.progress(full)
                stem = f"{self.counter:03d}_{_safe('_'.join(path + [name]))}"
                info = self.layer(layer, stem, node["blend"], node["opacity"])
                if info is None:
                    self.empty.append(full)
                    continue
                node["type"] = "layer"
                node.update(info)
            nodes.append(node)
        return nodes


def extract(psd_path, out_dir, translate=True, skip_pattern=names.DEFAULT_SKIP,
            tolerance=None, progress=None):
    """Returns a manifest dict; mask PNGs are written to out_dir."""
    from .fit import TOLERANCE
    os.makedirs(out_dir, exist_ok=True)
    psd = read_psd(psd_path)
    ex = _Extractor(psd, out_dir, translate, names.make_skip(skip_pattern),
                    TOLERANCE if tolerance is None else tolerance, progress)
    tree = ex.walk(psd.layers, [])
    return {"psd": os.path.basename(psd_path), "width": psd.width, "height": psd.height,
            "layers": tree, "dropped": ex.dropped, "empty": ex.empty, "folder": out_dir}
