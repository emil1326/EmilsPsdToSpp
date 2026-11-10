"""Reader/writer for Painter's paint/document.bin (format version 1).

The reader is a Python port of the document.bin parser from SubCraft
(https://github.com/Aalzard/subcraft, crates/mesh/src/spp/document.rs),
Copyright the SubCraft authors, used under the MIT License
(see LICENSE-SUBCRAFT). Changes from the original: ported to Python, added
field type 22 (16 bytes, Painter 12.2+), kept raw bool bytes, and added a
writer that round-trips byte for byte.
"""
import struct


class Obj:
    def __init__(self, cls, oid, fields):
        self.cls, self.id, self.fields = cls, oid, fields

    def get(self, n):
        for k, v in self.fields:
            if k == n:
                return v


class P:
    def __init__(self, d):
        self.d, self.pos, self.classes = d, 20, []

    def take(self, n):
        b = self.d[self.pos:self.pos + n]
        if len(b) != n:
            raise ValueError(f"short read at {self.pos}")
        self.pos += n
        return b

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def string(self):
        return self.take(self.u32()).decode("utf-8", "replace")

    def class_word(self):
        w = self.u32()
        if w != 0xFFFFFFFF:
            return w
        name = self.string()
        n = self.u32()
        props = [(self.string(), self.u32()) for _ in range(n)]
        self.classes.append((name, props))
        return len(self.classes) - 1

    def obj(self):
        oid = self.u32()
        return self.fields(self.class_word(), oid)

    def ref(self):
        if self.d[self.pos:self.pos + 4] == b"\0\0\0\0":
            self.pos += 4
            return None
        return self.obj()

    def fields(self, c, oid):
        name, props = self.classes[c]
        return Obj(name, oid, [(pn, self.value(pt)) for pn, pt in props])

    def value(self, t):
        f = lambda n: list(struct.unpack(f"<{n}f", self.take(4 * n)))
        if t in (1, 2, 3, 4):
            return f(t)
        if t == 13:
            return f(9)
        if t == 14:
            return f(16)
        if t in (5, 9):
            return struct.unpack("<i", self.take(4))[0]
        if t == 6:
            return list(struct.unpack("<ii", self.take(8)))
        if t == 10:
            return self.take(1)[0]  # raw byte: Painter writes true as 0xFF
        if t == 11:
            return self.u32()
        if t == 15:
            return list(struct.unpack("<II", self.take(8)))
        if t in (12, 21):
            return struct.unpack("<Q", self.take(8))[0]
        if t == 22:
            return self.take(16).hex()
        if t == 16:
            return self.string()
        if t in (18, 20):
            return self.ref()
        if t == 19:
            return [o for o in (self.ref() for _ in range(self.u32())) if o is not None]
        if t == 17:
            n = self.u32()
            if n == 0:
                return []
            c = self.class_word()
            return [self.fields(c, 0) for _ in range(n)]
        raise ValueError(f"unknown type {t} at {self.pos}")


def parse(d):
    assert d[:4] == struct.pack("<I", 0x1B7C2FDD), "bad magic"
    ver = struct.unpack("<I", d[4:8])[0]
    assert ver == 1, f"version {ver}"
    p = P(d)
    root = p.obj()
    assert p.pos == len(d), f"stopped at {p.pos} of {len(d)}"
    return root, p


# ---------------- writer (version 1) ----------------
class W:
    def __init__(self, classes_schema):
        # schema: class name -> props list, taken from the parse (so types are known)
        self.schema = classes_schema
        self.out = bytearray()
        self.index = {}

    def u32(self, v):
        self.out += struct.pack("<I", v & 0xFFFFFFFF)

    def string(self, s):
        b = s.encode("utf-8")
        self.u32(len(b)); self.out += b

    def class_word(self, name):
        if name in self.index:
            self.u32(self.index[name]); return
        self.u32(0xFFFFFFFF)
        self.string(name)
        props = self.schema[name]
        self.u32(len(props))
        for pn, pt in props:
            self.string(pn); self.u32(pt)
        self.index[name] = len(self.index)

    def obj(self, o):
        at = len(self.out)
        self.u32(0)  # patched with the end offset
        self.class_word(o.cls)
        self.fields(o)
        struct.pack_into("<I", self.out, at, len(self.out))

    def fields(self, o):
        types = dict(self.schema[o.cls])
        names = [pn for pn, _ in self.schema[o.cls]]
        vals = dict(o.fields)
        for pn in names:
            self.value(types[pn], vals[pn])

    def value(self, t, v):
        if t in (1, 2, 3, 4, 13, 14):
            self.out += struct.pack(f"<{len(v)}f", *v)
        elif t in (5, 9):
            self.out += struct.pack("<i", v)
        elif t == 6:
            self.out += struct.pack("<ii", *v)
        elif t == 10:
            self.out += bytes([(0xFF if v else 0) if isinstance(v, bool) else v])
        elif t == 11:
            self.u32(v)
        elif t == 15:
            self.out += struct.pack("<II", *v)
        elif t in (12, 21):
            self.out += struct.pack("<Q", v)
        elif t == 22:
            self.out += bytes.fromhex(v)
        elif t == 16:
            self.string(v)
        elif t in (18, 20):
            if v is None:
                self.u32(0)
            else:
                self.obj(v)
        elif t == 19:
            self.u32(len(v))
            for o in v:
                self.obj(o)
        elif t == 17:
            self.u32(len(v))
            if v:
                self.class_word(v[0].cls)
                for o in v:
                    self.fields(o)
        else:
            raise ValueError(t)


def write(root, parser, header):
    schema = {name: props for name, props in parser.classes}
    w = W(schema)
    w.out += b"\0" * 20
    w.obj(root)
    magic, ver, a, b, _ = header
    struct.pack_into("<5I", w.out, 0, magic, ver, a, b, len(w.index))
    return bytes(w.out)
