"""Just enough HDF5 to read one dataset of a .spp and overwrite it in place.

A .spp is an HDF5 file (superblock version 0, version 1 object headers, so no
metadata checksums). We follow the path to a dataset, find its raw chunks and
its "m3_x64_128" attribute, and rewrite them with data of the same size. No
h5py needed (loading a second HDF5 library inside Painter is asking for trouble).
"""
import struct

SIG = b"\x89HDF\r\n\x1a\n"


class Hdf5Error(Exception):
    pass


class _File:
    def __init__(self, buf):
        self.b = buf
        base = buf.find(SIG)
        if base < 0 or base % 512:
            raise Hdf5Error("not an HDF5 file")
        p = base + 8
        if buf[p] not in (0, 1):
            raise Hdf5Error(f"superblock version {buf[p]} not supported")
        sb_ver = buf[p]
        self.so, self.sl = buf[p + 5], buf[p + 6]
        p += 8 + 2 + 2 + 4
        if sb_ver == 1:
            p += 4
        self.base = self.off(p)
        p += 4 * self.so
        # root group symbol table entry
        self.root = self.off(p + self.so)

    def off(self, p):
        return int.from_bytes(self.b[p:p + self.so], "little")

    def length(self, p):
        return int.from_bytes(self.b[p:p + self.sl], "little")

    def addr(self, a):
        return self.base + a

    # --- object headers (version 1) ---
    def messages(self, oh):
        p = self.addr(oh)
        if self.b[p] != 1:
            raise Hdf5Error(f"object header version {self.b[p]} not supported")
        nmsg = struct.unpack_from("<H", self.b, p + 2)[0]
        size = struct.unpack_from("<I", self.b, p + 8)[0]
        blocks = [(p + 16, size)]
        out = []
        while blocks and len(out) < nmsg:
            q, size = blocks.pop(0)
            end = q + size
            while q + 8 <= end and len(out) < nmsg:
                mtype, msize = struct.unpack_from("<HH", self.b, q)
                data = q + 8
                out.append((mtype, data, msize))
                if mtype == 0x10:  # continuation
                    blocks.append((self.addr(self.off(data)), self.length(data + self.so)))
                q = data + msize
        return out

    # --- groups ---
    def children(self, oh):
        for mtype, d, _ in self.messages(oh):
            if mtype == 0x11:
                btree, heap = self.off(d), self.off(d + self.so)
                break
        else:
            raise Hdf5Error("group without a symbol table")
        h = self.addr(heap)
        if self.b[h:h + 4] != b"HEAP":
            raise Hdf5Error("bad local heap")
        heap_data = self.addr(self.off(h + 8 + 2 * self.sl))
        names = {}
        for snod in self._btree_leaves(btree, key_size=self.sl):
            s = self.addr(snod)
            if self.b[s:s + 4] != b"SNOD":
                raise Hdf5Error("bad symbol table node")
            n = struct.unpack_from("<H", self.b, s + 6)[0]
            e = s + 8
            for _ in range(n):
                name_off, ohdr = self.off(e), self.off(e + self.so)
                q = heap_data + name_off
                name = self.b[q:self.b.index(b"\0", q)].decode("utf-8")
                names[name] = ohdr
                e += 2 * self.so + 24
        return names

    def _btree_leaves(self, node, key_size):
        """Yields (child address) for level-0 entries of a v1 B-tree."""
        p = self.addr(node)
        if self.b[p:p + 4] != b"TREE":
            raise Hdf5Error("bad B-tree node")
        level = self.b[p + 5]
        used = struct.unpack_from("<H", self.b, p + 6)[0]
        q = p + 8 + 2 * self.so + key_size
        for _ in range(used):
            child = self.off(q)
            if level:
                yield from self._btree_leaves(child, key_size)
            else:
                yield child
            q += self.so + key_size

    def _chunks(self, node, rank):
        """Yields (offset_of_first_element, stored_size, filter_mask, address) for raw chunks."""
        key_size = 8 + 8 * (rank + 1)
        p = self.addr(node)
        if self.b[p:p + 4] != b"TREE" or self.b[p + 4] != 1:
            raise Hdf5Error("bad chunk B-tree node")
        level = self.b[p + 5]
        used = struct.unpack_from("<H", self.b, p + 6)[0]
        q = p + 8 + 2 * self.so
        for _ in range(used):
            csize, fmask = struct.unpack_from("<II", self.b, q)
            first = struct.unpack_from("<Q", self.b, q + 8)[0]
            child = self.off(q + key_size)
            if level:
                yield from self._chunks(child, rank)
            else:
                yield first, csize, fmask, child
            q += key_size + self.so


class Dataset:
    """A 1-D uint8 dataset of an HDF5 file held in memory (a bytearray)."""

    def __init__(self, buf, path):
        self.f = f = _File(buf)
        oh = f.root
        for part in path.strip("/").split("/"):
            kids = f.children(oh)
            if part not in kids:
                raise Hdf5Error(f"'{path}' not found")
            oh = kids[part]
        self.size = None
        self.layout = None
        self.attrs = {}
        for mtype, d, msize in f.messages(oh):
            if mtype == 0x01:
                ver, rank = buf[d], buf[d + 1]
                if rank != 1:
                    raise Hdf5Error("dataset is not 1-D")
                self.size = f.length(d + (8 if ver == 1 else 4))
            elif mtype == 0x0B:
                raise Hdf5Error("dataset is filtered (compressed)")
            elif mtype == 0x08:
                self.layout = (d, msize)
            elif mtype == 0x0C:
                self._attr(d)
        if self.size is None or self.layout is None:
            raise Hdf5Error("dataset header incomplete")
        self._read_layout()

    def _attr(self, d):
        b = self.f.b
        if b[d] != 1:
            return  # only version 1 attributes are written by these files
        nlen, tlen, slen = struct.unpack_from("<HHH", b, d + 2)
        pad = lambda n: (n + 7) & ~7
        name = b[d + 8:d + 8 + nlen].rstrip(b"\0").decode("utf-8")
        data = d + 8 + pad(nlen) + pad(tlen) + pad(slen)
        self.attrs[name] = data

    def _read_layout(self):
        b, f = self.f.b, self.f
        d, _ = self.layout
        if b[d] != 3:
            raise Hdf5Error(f"data layout version {b[d]} not supported")
        cls = b[d + 1]
        if cls == 1:  # contiguous
            self.pieces = [(0, self.size, f.addr(f.off(d + 2)))]
            return
        if cls != 2:
            raise Hdf5Error("compact datasets not supported")
        ndims = b[d + 2]
        btree = f.off(d + 3)
        chunk = struct.unpack_from("<I", b, d + 3 + f.so)[0]
        self.pieces = []
        for first, csize, fmask, addr in f._chunks(btree, ndims - 1):
            if fmask:
                raise Hdf5Error("filtered chunk")
            n = min(chunk, self.size - first)
            self.pieces.append((first, n, f.addr(addr)))
        self.pieces.sort()
        covered = sum(n for _, n, _ in self.pieces)
        if covered != self.size:
            raise Hdf5Error("missing chunks")

    def read(self):
        b = self.f.b
        return b"".join(bytes(b[a:a + n]) for _, n, a in self.pieces)

    def write(self, data):
        if len(data) != self.size:
            raise Hdf5Error(f"size changed ({self.size} -> {len(data)}), can't patch in place")
        b = self.f.b
        for first, n, a in self.pieces:
            b[a:a + n] = data[first:first + n]

    def set_attr_bytes(self, name, raw):
        p = self.attrs[name]
        self.f.b[p:p + len(raw)] = raw
