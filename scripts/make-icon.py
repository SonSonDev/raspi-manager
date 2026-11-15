"""Regenerate the app icon files from the SVG: assets/raspi-manager.ico and assets/raspi-manager.png.

Run when the SVG changes:  uv run python scripts/make-icon.py
Uses Qt only (no extra dependency). The .ico holds PNG-compressed images from 16 to 256 px.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "src" / "raspi" / "gui" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def build_ico(pngs: dict[int, bytes]) -> bytes:
    """ICONDIR + one ICONDIRENTRY per size, then the PNG payloads (Windows Vista and later)."""
    count = len(pngs)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries, payload = b"", b""
    for size, png in pngs.items():
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return header + entries + payload


def main() -> int:
    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(ASSETS / "raspi-manager.svg"))
    if not renderer.isValid():
        print("The SVG does not load", file=sys.stderr)
        return 1
    pngs = {size: render(renderer, size) for size in SIZES}
    (ASSETS / "raspi-manager.ico").write_bytes(build_ico(pngs))
    (ASSETS / "raspi-manager.png").write_bytes(pngs[256])
    print(
        f"Wrote {ASSETS / 'raspi-manager.ico'} ({len(SIZES)} sizes) and raspi-manager.png"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
