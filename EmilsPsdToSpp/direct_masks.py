"""Moves the PSD fills into their layer itself, inside a saved .spp.

Painter's Python API can only put a bitmap into a mask as a fill *effect*. In
the saved file, every layer mask starts with a hidden group: that's where your
brush strokes go when you paint a mask by hand. Moving our fill into that group
makes the mask part of the layer itself, with no effect row, and you can keep
painting on it. Paint layers work the same way: their content stack starts with
that hidden group too, so the PSD pixels (PIXELS_LABEL) go in there.

Only fills named MASK_LABEL / PIXELS_LABEL are touched. Close the .spp in Painter first.
"""
import struct

from . import hdf5_patch, murmur3, spp_document as doc

MASK_LABEL = "PSD mask"
PIXELS_LABEL = "PSD pixels"
DOCUMENT = "paint/document.bin"
# Painter checks each dataset against MurmurHash3 x64_128 with this seed.
CHECKSUM_SEED = 0xF13A0239


def _into_base(stack, label):
    """Moves fill effects named label into the stack's hidden base group. True if any moved."""
    items = stack.get("items") if stack is not None else None
    if not (items and items[0].cls == "DataActionGroup" and items[0].get("label") == ""):
        return False
    sub = items[0].get("subStack").get("items")
    fills = [e for e in items[1:] if e.cls == "DataActionFill" and e.get("label") == label]
    for e in fills:
        items.remove(e)
        sub.append(e)
    return bool(fills)


def _move(o, counter):
    if isinstance(o, list):
        for v in o:
            _move(v, counter)
        return
    if not isinstance(o, doc.Obj):
        return
    # masks (layers and folders) and paint layer contents start with the same hidden group
    counter[0] += _into_base(o.get("maskActions"), MASK_LABEL)
    if o.cls == "DataLayerColor":
        counter[1] += _into_base(o.get("actions"), PIXELS_LABEL)
    for _, v in o.fields:
        _move(v, counter)


def apply(spp_path):
    """Rewrites spp_path in place. Returns (masks moved, paint layer contents moved)."""
    with open(spp_path, "rb") as f:
        buf = bytearray(f.read())
    ds = hdf5_patch.Dataset(buf, DOCUMENT)
    data = ds.read()
    h1, h2 = murmur3.hash128(data, CHECKSUM_SEED)
    stored = bytes(buf[ds.attrs["m3_x64_128"]:ds.attrs["m3_x64_128"] + 16])
    if stored != struct.pack(">QQ", h1, h2):
        raise hdf5_patch.Hdf5Error("document checksum doesn't match, unknown file layout")

    root, parser = doc.parse(data)
    counter = [0, 0]
    _move(root, counter)
    if not any(counter):
        return tuple(counter)
    new = doc.write(root, parser, struct.unpack_from("<5I", data))
    doc.parse(new)  # must read back
    ds.write(new)
    ds.set_attr_bytes("m3_x64_128", struct.pack(">QQ", *murmur3.hash128(new, CHECKSUM_SEED)))

    tmp = spp_path + ".emilstmp"
    with open(tmp, "wb") as f:
        f.write(buf)
    import os
    os.replace(tmp, spp_path)
    return tuple(counter)
