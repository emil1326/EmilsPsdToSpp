"""Turns a PSD layer's pixels into flat colors + masks Painter can edit."""
import numpy as np

# Blend modes where one color + a soft mask reproduces a fading layer exactly:
# a multiply layer painted from white to X == X at mask t, screen from black to X == X at mask t.
NEUTRAL = {"Multiply": 255.0, "Darken": 255.0, "Screen": 0.0, "LinearDodge": 0.0, "Lighten": 0.0}
TOLERANCE = 6  # max visible error (0-255, after layer opacity) on 99% of the solid pixels


def mul(alpha, t):
    return np.clip(np.rint(alpha.astype(np.float32) * t), 0, 255).astype(np.uint8)


def fit_colors(rgb, alpha, blend, opacity=1.0, tolerance=TOLERANCE):
    """Returns (spec, t). spec is one of
        {"mode": "flat", "color": [r,g,b]}
        {"mode": "tint", "color": [r,g,b]}      -> mask = alpha * t
        {"mode": "two", "base": .., "top": ..}  -> base mask = alpha, top mask = alpha * t
        {"mode": "bitmap"}                      -> too many colors (or tolerance < 0), keep the pixels
    t is an HxW float array (0-1) or None."""
    if tolerance < 0:  # asked for exact pixels everywhere
        return {"mode": "bitmap"}, None
    solid = alpha > 64
    if not solid.any():
        solid = alpha > 0
    px = rgb[solid].astype(np.float32)
    rgb_f = rgb.astype(np.float32)

    def ok(recon):
        return np.percentile(np.abs(recon - px).max(1), 99) * opacity <= tolerance

    def c(v):
        return [int(round(x)) for x in np.clip(v, 0, 255)]

    med = np.median(px, 0)
    if ok(np.broadcast_to(med, px.shape)):
        return {"mode": "flat", "color": c(med)}, None

    if blend in NEUTRAL:
        w = np.full(3, NEUTRAL[blend], np.float32)
        norms = np.linalg.norm(px - w, axis=1)
        far = px[norms >= np.percentile(norms, 99.5)].mean(0)
        v = far - w
        if v @ v > 1:
            t_px = np.clip((px - w) @ v / (v @ v), 0, 1)
            if ok(w + t_px[:, None] * v):
                t = np.clip((rgb_f - w) @ v / (v @ v), 0, 1)
                return {"mode": "tint", "color": c(far)}, t

    mean = px.mean(0)
    _, _, vt = np.linalg.svd(px - mean, full_matrices=False)
    d = vt[0]
    s = (px - mean) @ d
    lo, hi = np.percentile(s, 0.5), np.percentile(s, 99.5)
    if hi - lo > 1:
        a, b = mean + lo * d, mean + hi * d
        t_px = np.clip((s - lo) / (hi - lo), 0, 1)
        if ok(a + t_px[:, None] * (b - a)):
            t = np.clip(((rgb_f - mean) @ d - lo) / (hi - lo), 0, 1)
            return {"mode": "two", "base": c(a), "top": c(b)}, t

    return {"mode": "bitmap"}, None
