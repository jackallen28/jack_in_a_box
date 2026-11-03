"""Readers for the page-raster formats AirPrint clients send: Apple URF ("UNIRAST") and PWG Raster ("RaS2").

Both are a small header per page followed by lines compressed with a PackBits-like scheme:
    line-repeat byte (this line is repeated n+1 times), then runs until the line is full:
        0..127   repeat the next pixel n+1 times
        128      the rest of the line is white
        129..255 copy the next 257-n pixels as they are
A "pixel" is bits-per-pixel/8 bytes (1 byte grey, 3 bytes RGB).
Pages come back as Pillow images.  Everything is size-checked so a bad file raises ``RasterError`` instead of eating the Pi's RAM.
"""
from __future__ import annotations

import struct

from PIL import Image

MAX_W, MAX_H, MAX_PIXELS = 12000, 40000, 80_000_000
URF_MAGIC, PWG_MAGICS = b"UNIRAST\x00", (b"RaS2", b"RaS3", b"RaSt")
PWG_HEADER = 1796


class RasterError(ValueError):
    pass


def _check(width: int, height: int) -> None:
    if not (0 < width <= MAX_W and 0 < height <= MAX_H and width * height <= MAX_PIXELS):
        raise RasterError(f"unreasonable page size {width}x{height}")


def _lines(buf: bytes, pos: int, line_bytes: int, unit: int, height: int) -> tuple[bytes, int]:
    """Decompress ``height`` lines of ``line_bytes`` bytes each; ``unit`` = bytes per pixel."""
    rows: list[bytes] = []
    n = len(buf)
    try:
        while len(rows) < height:
            repeat = buf[pos] + 1
            pos += 1
            line = bytearray()
            while len(line) < line_bytes:
                c = buf[pos]
                pos += 1
                if c < 128:
                    px = buf[pos:pos + unit]
                    if len(px) != unit:
                        raise RasterError("truncated raster data")
                    pos += unit
                    line += px * (c + 1)
                elif c == 128:
                    line += b"\xff" * (line_bytes - len(line))
                else:
                    count = (257 - c) * unit
                    chunk = buf[pos:pos + count]
                    if len(chunk) != count:
                        raise RasterError("truncated raster data")
                    pos += count
                    line += chunk
            rows.extend([bytes(line[:line_bytes])] * min(repeat, height - len(rows)))
            if pos > n:
                raise RasterError("truncated raster data")
    except IndexError:
        raise RasterError("truncated raster data") from None
    return b"".join(rows), pos


def _to_image(data: bytes, width: int, height: int, bits_per_pixel: int, invert_1bit: bool = False) -> Image.Image:
    if bits_per_pixel == 8:
        return Image.frombytes("L", (width, height), data)
    if bits_per_pixel == 24:
        return Image.frombytes("RGB", (width, height), data)
    if bits_per_pixel == 1:
        im = Image.frombytes("1", (width, height), data, "raw", "1;I" if invert_1bit else "1")
        return im.convert("L")
    raise RasterError(f"unsupported {bits_per_pixel}-bit pixels")


def read_urf(buf: bytes, max_pages: int = 50) -> list[Image.Image]:
    if buf[:8] != URF_MAGIC or len(buf) < 12:
        raise RasterError("not a URF file")
    pages = struct.unpack(">I", buf[8:12])[0]
    pos, out = 12, []
    for _ in range(min(pages or 1, max_pages)):
        if pos + 32 > len(buf):
            break
        h = buf[pos:pos + 32]
        pos += 32
        bpp = h[0]
        width, height = struct.unpack(">II", h[12:20])
        _check(width, height)
        if bpp not in (8, 24):
            raise RasterError(f"unsupported {bpp}-bit URF")
        data, pos = _lines(buf, pos, width * bpp // 8, bpp // 8, height)
        out.append(_to_image(data, width, height, bpp))
    if not out:
        raise RasterError("no pages")
    return out


def read_pwg(buf: bytes, max_pages: int = 50) -> list[Image.Image]:
    if buf[:4] not in PWG_MAGICS:
        raise RasterError("not a PWG raster file")
    pos, out = 4, []
    while pos + PWG_HEADER <= len(buf) and len(out) < max_pages:
        h = buf[pos:pos + PWG_HEADER]
        pos += PWG_HEADER
        width, height = struct.unpack(">II", h[372:380])
        bits_per_pixel, bytes_per_line = struct.unpack(">II", h[388:396])
        _check(width, height)
        if bits_per_pixel not in (1, 8, 24) or not 0 < bytes_per_line <= width * 3 + 8:
            raise RasterError(f"unsupported PWG page ({bits_per_pixel} bits)")
        data, pos = _lines(buf, pos, bytes_per_line, max(1, bits_per_pixel // 8), height)
        if bits_per_pixel == 1 and bytes_per_line * 8 != width:             # rows are padded to whole bytes
            im = Image.frombytes("1", (bytes_per_line * 8, height), data, "raw", "1;I").crop((0, 0, width, height)).convert("L")
        else:
            im = _to_image(data, width, height, bits_per_pixel, invert_1bit=True)
        out.append(im)
    if not out:
        raise RasterError("no pages")
    return out
