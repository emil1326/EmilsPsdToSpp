"""MurmurHash3 x64_128 in plain Python (Painter's Python has no mmh3)."""
import struct

_M = (1 << 64) - 1
_C1 = 0x87C37B91114253D5
_C2 = 0x4CF5AD432745937F


def _rotl(x, r):
    return ((x << r) | (x >> (64 - r))) & _M


def _fmix(k):
    k ^= k >> 33
    k = (k * 0xFF51AFD7ED558CCD) & _M
    k ^= k >> 33
    k = (k * 0xC4CEB9FE1A85EC53) & _M
    k ^= k >> 33
    return k


def hash128(data, seed=0):
    """Returns (h1, h2), the two 64-bit halves, like mmh3.hash128(...) & mask / >> 64."""
    n = len(data)
    nblocks = n // 16
    h1 = h2 = seed & 0xFFFFFFFF
    blocks = struct.unpack_from(f"<{nblocks * 2}Q", data) if nblocks else ()
    for i in range(0, nblocks * 2, 2):
        k1 = (blocks[i] * _C1) & _M
        k1 = _rotl(k1, 31)
        k1 = (k1 * _C2) & _M
        h1 ^= k1
        h1 = _rotl(h1, 27)
        h1 = (h1 + h2) & _M
        h1 = (h1 * 5 + 0x52DCE729) & _M

        k2 = (blocks[i + 1] * _C2) & _M
        k2 = _rotl(k2, 33)
        k2 = (k2 * _C1) & _M
        h2 ^= k2
        h2 = _rotl(h2, 31)
        h2 = (h2 + h1) & _M
        h2 = (h2 * 5 + 0x38495AB5) & _M

    tail = data[nblocks * 16:]
    k1 = k2 = 0
    for i in range(len(tail) - 1, 7, -1):
        k2 ^= tail[i] << ((i - 8) * 8)
    if len(tail) > 8:
        k2 = (k2 * _C2) & _M
        k2 = _rotl(k2, 33)
        k2 = (k2 * _C1) & _M
        h2 ^= k2
    for i in range(min(len(tail), 8) - 1, -1, -1):
        k1 ^= tail[i] << (i * 8)
    if tail:
        k1 = (k1 * _C1) & _M
        k1 = _rotl(k1, 31)
        k1 = (k1 * _C2) & _M
        h1 ^= k1

    h1 ^= n
    h2 ^= n
    h1 = (h1 + h2) & _M
    h2 = (h2 + h1) & _M
    h1 = _fmix(h1)
    h2 = _fmix(h2)
    h1 = (h1 + h2) & _M
    h2 = (h2 + h1) & _M
    return h1, h2
