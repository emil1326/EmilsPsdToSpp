"""Small PSD reader: pure Python + numpy, nothing else outside the stdlib.

Reads PSD version 1, RGB or grayscale, 8 or 16 bit. Layers, groups, masks.
"""

import struct
import zlib

import numpy as np

_BLEND_KEYS = {
    b"pass": "PASS_THROUGH", b"norm": "NORMAL", b"diss": "DISSOLVE",
    b"dark": "DARKEN", b"mul ": "MULTIPLY", b"idiv": "COLOR_BURN",
    b"lbrn": "LINEAR_BURN", b"dkCl": "DARKER_COLOR", b"lite": "LIGHTEN",
    b"scrn": "SCREEN", b"div ": "COLOR_DODGE", b"lddg": "LINEAR_DODGE",
    b"lgCl": "LIGHTER_COLOR", b"over": "OVERLAY", b"sLit": "SOFT_LIGHT",
    b"hLit": "HARD_LIGHT", b"vLit": "VIVID_LIGHT", b"lLit": "LINEAR_LIGHT",
    b"pLit": "PIN_LIGHT", b"hMix": "HARD_MIX", b"diff": "DIFFERENCE",
    b"smud": "EXCLUSION", b"fsub": "SUBTRACT", b"fdiv": "DIVIDE",
    b"hue ": "HUE", b"sat ": "SATURATION", b"colr": "COLOR",
    b"lum ": "LUMINOSITY",
}

COLOR_MODES = {0: "BITMAP", 1: "GRAYSCALE", 2: "INDEXED", 3: "RGB",
               4: "CMYK", 7: "MULTICHANNEL", 8: "DUOTONE", 9: "LAB"}


class PsdError(Exception):
    pass


def _blend_name(key):
    return _BLEND_KEYS.get(key, "NORMAL")


# ---------------------------------------------------------------- decoding

def _unpack_rle(src, h, row_bytes, counts):
    """PackBits for h rows at once. Steps all rows one run at a time in numpy,
    then expands every run in a single gather."""
    row_start = np.cumsum(counts) - counts
    row_end = row_start + counts
    rows = np.arange(h, dtype=np.int64)
    pos = row_start.astype(np.int64)
    out_pos = np.zeros(h, dtype=np.int64)
    end = row_end.astype(np.int64)

    run_row, run_out, run_len, run_src, run_lit = [], [], [], [], []
    n = len(src)
    while rows.size:
        keep = pos < end
        if not keep.all():
            rows, pos, out_pos, end = rows[keep], pos[keep], out_pos[keep], end[keep]
            if not rows.size:
                break
        b = src[np.minimum(pos, n - 1)].astype(np.int64)
        lit = b < 128
        rep = b > 128
        length = np.where(lit, b + 1, np.where(rep, 257 - b, 0))
        length = np.minimum(length, row_bytes - out_pos)
        run_row.append(rows)
        run_out.append(out_pos)
        run_len.append(length)
        run_src.append(pos + 1)
        run_lit.append(lit)
        out_pos = out_pos + length
        pos = np.where(lit, pos + 2 + b, np.where(rep, pos + 2, pos + 1))

    out = np.zeros(h * row_bytes, dtype=np.uint8)
    if not run_row:
        return out
    row = np.concatenate(run_row)
    start = row * row_bytes + np.concatenate(run_out)
    length = np.concatenate(run_len)
    srcpos = np.concatenate(run_src)
    lit = np.concatenate(run_lit)
    nz = length > 0
    if not nz.all():
        start, length, srcpos, lit = start[nz], length[nz], srcpos[nz], lit[nz]
    order = np.argsort(start, kind="stable")
    start, length, srcpos, lit = start[order], length[order], srcpos[order], lit[order]
    total = int(length.sum())
    idt = np.int32 if max(total, n, h * row_bytes) < 2**31 - 1 else np.int64
    csum = np.cumsum(length) - length
    litv = lit.astype(idt)
    ar = np.arange(total, dtype=idt)
    sidx = np.repeat((srcpos - csum * lit).astype(idt), length)
    sidx += ar * np.repeat(litv, length)
    np.clip(sidx, 0, n - 1, out=sidx)
    vals = src[sidx]
    if total == h * row_bytes and bool((start == csum).all()):
        return vals
    dest = np.repeat((start - csum).astype(idt), length) + ar
    out[dest] = vals
    return out


def _decode_channel(data, off, size, w, h, depth):
    """Decode one channel to a uint8 (h, w) array (16 bit keeps the high byte)."""
    bpp = 2 if depth == 16 else 1
    row_bytes = w * bpp
    comp = struct.unpack_from(">H", data, off)[0]
    body = off + 2
    end = off + size
    if comp == 0:
        raw = np.frombuffer(data, np.uint8, h * row_bytes, body)
    elif comp == 1:
        counts = np.frombuffer(data, ">u2", h, body).astype(np.int64)
        s = body + 2 * h
        src = np.frombuffer(data, np.uint8, int(counts.sum()), s)
        raw = _unpack_rle(src, h, row_bytes, counts)
    elif comp in (2, 3):
        raw = np.frombuffer(zlib.decompress(data[body:end]), np.uint8)
        raw = raw[:h * row_bytes]
        if raw.size < h * row_bytes:
            raw = np.concatenate([raw, np.zeros(h * row_bytes - raw.size, np.uint8)])
        if comp == 3:
            if depth == 16:
                a = raw.view(">u2").reshape(h, w).astype(np.uint16)
                a = np.cumsum(a, axis=1, dtype=np.uint16)
                raw = a.astype(">u2").view(np.uint8)
            else:
                raw = np.cumsum(raw.reshape(h, w), axis=1, dtype=np.uint8)
    else:
        raise PsdError("Unsupported channel compression %d" % comp)
    raw = raw.reshape(h, row_bytes)
    if depth == 16:
        return raw[:, 0::2]
    return raw


# ------------------------------------------------------------------ model

class Layer:
    def __init__(self, psd):
        self._psd = psd
        self.name = ""
        self.visible = True
        self.opacity = 255
        self.blend = "NORMAL"
        self.clipping = False
        self.is_group = False
        self.children = []
        self.left = self.top = self.right = self.bottom = 0
        self._channels = {}   # id -> (offset, size)
        self._mask = None     # dict(left, top, right, bottom, background, disabled)

    def _chan(self, cid, w, h):
        off, size = self._channels[cid]
        return _decode_channel(self._psd._data, off, size, w, h, self._psd.depth)

    def pixels(self):
        w, h = self.right - self.left, self.bottom - self.top
        if self.is_group or w <= 0 or h <= 0:
            return None
        ch = self._channels
        gray = self._psd.color_mode == 1
        need = (0,) if gray else (0, 1, 2)
        if any(c not in ch for c in need):
            return None
        out = np.empty((h, w, 4), dtype=np.uint8)
        if gray:
            out[:, :, :3] = self._chan(0, w, h)[:, :, None]
        else:
            for i in need:
                out[:, :, i] = self._chan(i, w, h)
        if -1 in ch:
            out[:, :, 3] = self._chan(-1, w, h)
        else:
            out[:, :, 3] = 255
        return out, self.left, self.top

    def mask(self):
        m = self._mask
        if m is None or -2 not in self._channels:
            return None
        w, h = m["right"] - m["left"], m["bottom"] - m["top"]
        if w <= 0 or h <= 0:
            return None
        data = np.ascontiguousarray(self._chan(-2, w, h))
        return dict(data=data, left=m["left"], top=m["top"],
                    background=m["background"], disabled=m["disabled"])


class Psd:
    def __init__(self):
        self.width = self.height = self.depth = self.color_mode = 0
        self.layers = []
        self._data = b""


# ----------------------------------------------------------------- parsing

def _read_layer_info(data, pos, psd):
    """Parse layer count + records + channel data at pos. Returns flat layer
    list (file order, bottom -> top) and end position."""
    count = struct.unpack_from(">h", data, pos)[0]
    pos += 2
    count = abs(count)
    recs = []
    for _ in range(count):
        top, left, bottom, right, nch = struct.unpack_from(">iiiiH", data, pos)
        pos += 18
        chans = []
        for _ in range(nch):
            cid, clen = struct.unpack_from(">hI", data, pos)
            pos += 6
            chans.append((cid, clen))
        sig, key, opacity, clipping, flags = struct.unpack_from(">4s4sBBB", data, pos)
        if sig != b"8BIM":
            raise PsdError("Bad layer record signature")
        pos += 12  # includes filler byte
        extra_len = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        extra_end = pos + extra_len

        lyr = Layer(psd)
        lyr.left, lyr.top, lyr.right, lyr.bottom = left, top, right, bottom
        lyr.opacity = opacity
        lyr.blend = _blend_name(key)
        lyr.clipping = clipping != 0
        lyr.visible = not (flags & 2)

        mlen = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        if mlen >= 18:
            mt, ml, mb, mr, bg, mflags = struct.unpack_from(">iiiiBB", data, pos)
            lyr._mask = dict(left=ml, top=mt, right=mr, bottom=mb,
                             background=bg, disabled=bool(mflags & 2))
        pos += mlen
        blen = struct.unpack_from(">I", data, pos)[0]
        pos += 4 + blen
        nlen = data[pos]
        pname = bytes(data[pos + 1:pos + 1 + nlen]).decode("mac_roman", "replace")
        pos += (1 + nlen + 3) & ~3
        lyr.name = pname

        divider = None
        nested = None
        while pos + 12 <= extra_end:
            sig = bytes(data[pos:pos + 4])
            if sig not in (b"8BIM", b"8B64"):
                pos += 1
                continue
            k = bytes(data[pos + 4:pos + 8])
            ln = struct.unpack_from(">I", data, pos + 8)[0]
            bs = pos + 12
            if k == b"luni":
                n = struct.unpack_from(">I", data, bs)[0]
                lyr.name = bytes(data[bs + 4:bs + 4 + n * 2]).decode("utf-16-be", "replace")
            elif k in (b"lsct", b"lsdk"):
                kind = struct.unpack_from(">I", data, bs)[0]
                bkey = None
                if ln >= 12 and data[bs + 4:bs + 8] == b"8BIM":
                    bkey = bytes(data[bs + 8:bs + 12])
                if k == b"lsct":
                    divider = (kind, bkey)
                else:
                    nested = (kind, bkey)
            pos = bs + ln
        pos = extra_end
        recs.append((lyr, chans, divider if nested is None else nested))

    # channel image data
    for lyr, chans, _ in recs:
        for cid, clen in chans:
            lyr._channels[cid] = (pos, clen)
            pos += clen
    return recs, pos


def _build_tree(recs):
    root = []
    stack = [root]
    open_groups = []
    for lyr, _, div in recs:
        kind = div[0] if div else 0
        if kind == 3:
            g = Layer(lyr._psd)
            g.is_group = True
            stack.append(g.children)
            open_groups.append(g)
        elif kind in (1, 2) and open_groups:
            g = open_groups.pop()
            stack.pop()
            lyr.is_group = True
            lyr.children = g.children
            if div[1] is not None:
                lyr.blend = _blend_name(div[1])
            boxes = [(c.left, c.top, c.right, c.bottom) for c in lyr.children
                     if c.right > c.left and c.bottom > c.top]
            if boxes:
                lyr.left = min(b[0] for b in boxes)
                lyr.top = min(b[1] for b in boxes)
                lyr.right = max(b[2] for b in boxes)
                lyr.bottom = max(b[3] for b in boxes)
            else:
                lyr.left = lyr.top = lyr.right = lyr.bottom = 0
            stack[-1].append(lyr)
        else:
            stack[-1].append(lyr)
    while open_groups:  # unbalanced: keep the children, drop the group
        g = open_groups.pop()
        stack.pop()
        stack[-1].extend(g.children)
    return root


def read_psd(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"8BPS":
        raise PsdError("Not a PSD file")
    version, _, nch, h, w, depth, mode = struct.unpack_from(">H6sHIIHH", data, 4)
    if version == 2:
        raise PsdError("PSB (large document) files are not supported")
    if version != 1:
        raise PsdError("Unknown PSD version %d" % version)
    if mode not in (1, 3):
        raise PsdError("Unsupported color mode %s (only RGB and grayscale)"
                       % COLOR_MODES.get(mode, mode))
    if depth not in (8, 16):
        raise PsdError("Unsupported bit depth %d (only 8 and 16)" % depth)

    psd = Psd()
    psd.width, psd.height, psd.depth, psd.color_mode = w, h, depth, mode
    psd._data = data

    pos = 26
    pos += 4 + struct.unpack_from(">I", data, pos)[0]   # color mode data
    pos += 4 + struct.unpack_from(">I", data, pos)[0]   # image resources
    sec_len = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    sec_end = pos + sec_len
    if sec_len == 0:
        return psd

    info_len = struct.unpack_from(">I", data, pos)[0]
    info_start = pos + 4
    recs = []
    if info_len > 0:
        recs, _ = _read_layer_info(data, info_start, psd)
    pos = info_start + info_len
    if pos + 4 <= sec_end:
        pos += 4 + struct.unpack_from(">I", data, pos)[0]  # global layer mask

    # global tagged blocks; 16/32 bit layers live in Lr16 / Lr32
    while not recs and pos + 12 <= sec_end:
        sig = bytes(data[pos:pos + 4])
        if sig not in (b"8BIM", b"8B64"):
            pos += 1
            continue
        k = bytes(data[pos + 4:pos + 8])
        ln = struct.unpack_from(">I", data, pos + 8)[0]
        if k in (b"Lr16", b"Lr32"):
            recs, _ = _read_layer_info(data, pos + 12, psd)
            break
        pos += 12 + ln

    psd.layers = _build_tree(recs)
    return psd
