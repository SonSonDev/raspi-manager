import importlib.util
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raspi.gui import icon

ROOT = Path(__file__).resolve().parent.parent
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def test_the_icon_files_exist_and_the_ico_holds_every_size_as_png():
    data = icon.ICON_FILE.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(SIZES))
    found = []
    for n in range(count):
        width, height, _, _, planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * n : 22 + 16 * n]
        )
        assert width == height and planes == 1 and bpp == 32
        assert data[offset : offset + 8] == PNG_SIGNATURE and len(data) >= offset + size
        found.append(width or 256)
    assert tuple(found) == SIZES
    assert (
        (icon.ASSETS / "raspi-manager.svg")
        .read_text(encoding="utf-8")
        .startswith("<svg")
    )
    assert (icon.ASSETS / "raspi-manager.png").read_bytes().startswith(PNG_SIGNATURE)


def test_the_icon_loads_in_qt_with_its_sizes():
    QApplication.instance() or QApplication([])
    qt_icon = icon.app_icon()
    assert not qt_icon.isNull()
    assert 256 in {s.width() for s in qt_icon.availableSizes()}


def test_the_generator_rebuilds_the_same_ico(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "make_icon", ROOT / "scripts" / "make-icon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pngs = {
        s: icon.ASSETS.joinpath("raspi-manager.png").read_bytes() for s in (16, 256)
    }
    built = module.build_ico(pngs)
    count = struct.unpack("<H", built[4:6])[0]
    assert count == 2 and built[6] == 16 and built[22] == 0  # 256 is stored as 0


def test_the_shortcut_script_uses_the_icon():
    script = (ROOT / "scripts" / "install-shortcut.ps1").read_text(encoding="utf-8")
    assert "IconLocation" in script and "raspi-manager.ico" in script
