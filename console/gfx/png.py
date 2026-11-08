"""Minimal PNG decoder, standard library only.

Console ships Chrome's own sprite sheet and decodes it at start-up, so there
is no Pillow dependency. Handles every non-interlaced PNG flavour that
matters here: grey, RGB, palette (with ``tRNS``), grey+alpha and RGBA, at the
usual bit depths. Output is always 8-bit RGBA.
"""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PngError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        else:
            raise PngError(f"bad filter type {ftype}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return out


def _samples(row: bytes | bytearray, width: int, depth: int, channels: int) -> list[int]:
    """Expand one unfiltered scanline into a list of per-channel samples."""
    count = width * channels
    if depth == 8:
        return list(row[:count])
    if depth == 16:
        return [row[i * 2] for i in range(count)]  # keep the high byte
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    out: list[int] = []
    for i in range(count):
        byte = row[i // per_byte]
        shift = 8 - depth * (i % per_byte + 1)
        out.append((byte >> shift) & mask)
    return out


def decode(data: bytes) -> tuple[int, int, bytes]:
    """Decode a PNG into ``(width, height, rgba_bytes)``."""
    if not data.startswith(_SIGNATURE):
        raise PngError("not a PNG file")
    pos = len(_SIGNATURE)
    ihdr = None
    palette: list[tuple[int, int, int]] = []
    trns = b""
    idat = bytearray()
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            palette = [tuple(body[i : i + 3]) for i in range(0, len(body), 3)]
        elif ctype == b"tRNS":
            trns = bytes(body)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if ihdr is None:
        raise PngError("missing IHDR")
    width, height, depth, ctype, _comp, _filt, interlace = ihdr
    if interlace:
        raise PngError("interlaced PNGs are not supported")
    if ctype not in _CHANNELS:
        raise PngError(f"unsupported colour type {ctype}")
    channels = _CHANNELS[ctype]
    bits_per_pixel = channels * depth
    stride = (width * bits_per_pixel + 7) // 8
    bpp = max(1, bits_per_pixel // 8)
    pixels = _unfilter(zlib.decompress(bytes(idat)), height, stride, bpp)

    out = bytearray(width * height * 4)
    key = None
    if ctype in (0, 2) and trns:
        key = struct.unpack(">" + "H" * (len(trns) // 2), trns)
    scale = 255 // ((1 << min(depth, 8)) - 1) if depth < 8 else 1
    o = 0
    for y in range(height):
        row = pixels[y * stride : (y + 1) * stride]
        s = _samples(row, width, depth, channels)
        for x in range(width):
            if ctype == 3:
                idx = s[x]
                r, g, b = palette[idx]
                a = trns[idx] if idx < len(trns) else 255
            elif ctype == 0:
                v = s[x] * scale
                r = g = b = v
                a = 0 if key is not None and s[x] == key[0] else 255
            elif ctype == 2:
                r, g, b = s[x * 3], s[x * 3 + 1], s[x * 3 + 2]
                a = 255
                if key is not None and (s[x * 3], s[x * 3 + 1], s[x * 3 + 2]) == tuple(k >> (16 - depth) if depth < 16 else k >> 8 for k in key):
                    a = 0
            elif ctype == 4:
                r = g = b = s[x * 2]
                a = s[x * 2 + 1]
            else:
                r, g, b, a = s[x * 4 : x * 4 + 4]
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a
            o += 4
    return width, height, bytes(out)
