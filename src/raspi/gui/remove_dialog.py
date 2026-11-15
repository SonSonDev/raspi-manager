"""Choose what to remove for an app, and confirm by typing its name."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from raspi.core.remove import INVENTORY, LOCAL
from raspi.services.remove import RemovePlan


class RemoveAppDialog(QDialog):
    """On accept, `result_value` is the set of item keys the developer ticked."""

    def __init__(self, plan: RemovePlan, host_name: str, parent=None) -> None:
        super().__init__(parent)
        self._plan = plan
        self.result_value: set[str] = set()
        name = plan.app.name
        self.setWindowTitle(f"Remove {name} - {host_name}")
        self.resize(620, 520)

        intro = QLabel(
            f"Tick what should go from <b>{host_name}</b>. Only the app's list entry and "
            "its unit are ticked at first; everything else is up to you."
        )
        intro.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)

        self._boxes: dict[str, QCheckBox] = {}
        for item in plan.items:
            box = QCheckBox(item.label)
            box.setChecked(item.ticked and item.available)
            box.setEnabled(item.available)
            box.toggled.connect(self._changed)
            self._boxes[item.key] = box
            layout.addWidget(box)
            if item.note:
                note = QLabel(item.note)
                note.setWordWrap(True)
                note.setContentsMargins(24, 0, 0, 0)
                note.setEnabled(item.available)
                layout.addWidget(note)

        kept = [r.path for r in plan.app.requires]
        never = (
            "Never touched: anything outside the home folder, and the Cloudflare route and "
            "tunnel (remove those from the Cloudflare window)."
        )
        if kept:
            never += (
                f" The app's data, listed as prerequisites, stays: {', '.join(kept)}."
            )
        policy = QLabel(never)
        policy.setWordWrap(True)
        layout.addWidget(policy)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Commands run on the Pi")
        font = QFont("Cascadia Mono")
        font.setPointSize(9)
        self.preview.setFont(font)
        layout.addWidget(QLabel("Commands run on the Pi:"))
        layout.addWidget(self.preview, 1)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setAccessibleName("Type the app name to confirm")
        self.confirm_edit.setPlaceholderText(f"type {name} to confirm")
        self.confirm_edit.textChanged.connect(self._changed)
        layout.addWidget(self.confirm_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.remove_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.remove_button.setText("Remove")
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setDefault(True)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._changed()

    def chosen(self) -> set[str]:
        return {k for k, b in self._boxes.items() if b.isChecked() and b.isEnabled()}

    def _changed(self, *_: object) -> None:
        chosen = self.chosen()
        on_pi = chosen - {INVENTORY, LOCAL}
        self.preview.setPlainText(
            self._plan.script_for(chosen)
            if on_pi
            else "(nothing runs on the Pi for this selection)"
        )
        typed = self.confirm_edit.text().strip() == self._plan.app.name
        self.remove_button.setEnabled(bool(chosen) and typed)

    def _accept(self) -> None:
        self.result_value = self.chosen()
        self.accept()
