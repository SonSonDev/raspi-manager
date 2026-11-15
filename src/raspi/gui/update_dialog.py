"""Choose what to update an app to: a ref (branch, tag, commit), or go back."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from raspi.core.models import AppRef
from raspi.core.update import UpdateError, check_ref


class UpdateDialog(QDialog):
    """On accept, `result_value` is (ref, rollback)."""

    def __init__(self, app: AppRef, host_name: str, parent=None) -> None:
        super().__init__(parent)
        self.result_value: tuple[str, bool] | None = None
        self.setWindowTitle(f"Update {app.name} - {host_name}")
        self.resize(520, 220)
        intro = QLabel(
            "Checks out the chosen version, runs the build and restarts the app if it was "
            "running. The next window shows what changes before anything is done. Local "
            "changes on the Pi are never overwritten."
        )
        intro.setWordWrap(True)
        self.ref_edit = QLineEdit()
        self.ref_edit.setAccessibleName("Version to update to")
        self.ref_edit.setPlaceholderText(
            "empty: latest of the default branch · or a branch, tag or commit"
        )
        self.rollback_box = QCheckBox("Go back to the previous version instead")
        can_go_back = bool(app.previous or app.built)
        self.rollback_box.setEnabled(can_go_back)
        self.rollback_box.setToolTip(
            "Returns to the revision built before the last update (or to the last build "
            "after a failed update)"
            if can_go_back
            else "No earlier build is recorded for this app"
        )
        self.rollback_box.toggled.connect(
            lambda checked: self.ref_edit.setEnabled(not checked)
        )
        self.error = QLabel("")
        self.error.setObjectName("banner")
        self.error.setVisible(False)

        form = QFormLayout()
        form.addRow("Update to", self.ref_edit)
        form.addRow("", self.rollback_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Check…")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        rollback = self.rollback_box.isChecked()
        try:
            ref = "" if rollback else check_ref(self.ref_edit.text())
        except UpdateError as exc:
            self.error.setText(str(exc))
            self.error.setVisible(True)
            return
        self.result_value = (ref, rollback)
        self.accept()
