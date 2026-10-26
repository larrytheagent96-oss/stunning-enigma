"""Draw the app icon (a play symbol on a purple-blue rounded square) as icon.png, using only the standard library."""

import struct
import zlib
from pathlib import Path

SIZE = 1024


def pixel(x: float, y: float) -> tuple[int, int, int, int]:
    s = SIZE
    # Rounded square with an anti-aliased edge.
    margin, radius = s * 0.06, s * 0.22
    cx = min(max(x, margin + radius), s - margin - radius)
    cy = min(max(y, margin + radius), s - margin - radius)
    dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    alpha = max(0.0, min(1.0, radius - dist + 0.5))
    if alpha == 0:
        return 0, 0, 0, 0
    # Diagonal gradient from purple (#7b6cff) to blue (#4f8cff).
    t = (x + y) / (2 * s)
    r, g, b = (round(a + (c - a) * t) for a, c in ((0x7B, 0x4F), (0x6C, 0x8C), (0xFF, 0xFF)))
    # White play triangle, slightly right of centre so it looks centred.
    left, right, top, bottom = s * 0.38, s * 0.72, s * 0.29, s * 0.71
    if left <= x <= right:
        half = (bottom - top) / 2 * (right - x) / (right - left)
        mid = s / 2
        edge = min(x - left, half - abs(y - mid))
        cover = max(0.0, min(1.0, edge + 0.5))
        r, g, b = (round(v + (255 - v) * cover) for v in (r, g, b))
    return r, g, b, round(alpha * 255)


def write_png(path: Path) -> None:
    rows = []
    for y in range(SIZE):
        row = bytearray([0])
        for x in range(SIZE):
            row.extend(pixel(x + 0.5, y + 0.5))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    write_png(Path(__file__).with_name("icon.png"))
