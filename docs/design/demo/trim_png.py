"""Schneidet leeren Hintergrund unten von einem PNG ab (nur Standardbibliothek).
Aufruf: python trim_png.py datei.png [Rand=24]. Gibt 'FULL' aus, wenn nichts leer war."""
import struct
import sys
import zlib


def main(path, margin=24):
    data = open(path, "rb").read()
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            ihdr = body
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", ihdr)
    assert depth == 8 and inter == 0 and ctype in (2, 6), (depth, ctype, inter)
    bpp = 3 if ctype == 2 else 4
    stride = w * bpp
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                cur[i] = (cur[i] + a) & 255
            elif f == 2:
                cur[i] = (cur[i] + b) & 255
            elif f == 3:
                cur[i] = (cur[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 255
        rows.append(cur)
        prev = cur
    bg = bytes(rows[-1][:bpp])
    last = h - 1
    while last > 0 and all(bytes(rows[last][i:i + bpp]) == bg for i in range(0, stride, bpp)):
        last -= 1
    if last >= h - 2:
        print("FULL", path)
        return
    new_h = min(h, last + 1 + int(margin))
    out = b"".join(b"\x00" + bytes(r) for r in rows[:new_h])

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, new_h, 8, ctype, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(out, 9)) + chunk(b"IEND", b""))
    open(path, "wb").write(png)


main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 24)
