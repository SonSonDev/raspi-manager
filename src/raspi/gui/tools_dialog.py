"""Pick which tools to install on a Pi: what it has now, what is available."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from raspi.core.tools import FFMPEG, YTDLP
from raspi.services.tools import ToolState

TITLES = {
    FFMPEG: "ffmpeg (from Debian, through apt)",
    YTDLP: "yt-dlp (official release)",
}


class ToolsDialog(QDialog):
    """On accept, `result_value` is the set of tools ticked."""

    def __init__(self, host_name: str, state: ToolState, parent=None) -> None:
        super().__init__(parent)
        self.result_value: set[str] = set()
        self.setWindowTitle(f"Install tools - {host_name}")
        self.resize(520, 260)
        intro = QLabel(
            f"Tools some apps need on <b>{host_name}</b>. What is missing or out of date "
            "is ticked. yt-dlp is checked against the release's SHA-256 and run once "
            "before it replaces anything; install again later to update it."
        )
        intro.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        self._boxes: dict[str, QCheckBox] = {}
        lines = state.lines()
        needed = state.needed()
        for key in (FFMPEG, YTDLP):
            box = QCheckBox(f"{TITLES[key]}: {lines[key]}")
            box.setChecked(key in needed)
            box.toggled.connect(self._changed)
            self._boxes[key] = box
            layout.addWidget(box)
        if not state.latest:
            note = QLabel(
                "The latest yt-dlp version could not be read from GitHub just now."
            )
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Check…")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._changed()

    def chosen(self) -> set[str]:
        return {k for k, b in self._boxes.items() if b.isChecked()}

    def _changed(self, *_: object) -> None:
        self.ok_button.setEnabled(bool(self.chosen()))

    def _accept(self) -> None:
        self.result_value = self.chosen()
        self.accept()
