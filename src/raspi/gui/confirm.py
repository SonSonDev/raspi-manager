"""The one confirmation every change to a Pi goes through: what changes, then the exact script."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QMessageBox, QWidget

from raspi import APP_NAME


def confirm_change(
    parent: QWidget,
    title: str,
    steps: list[str],
    warnings: list[str],
    details: str,
    button: str,
) -> bool:
    """True when the developer clicked `button`. Cancel is the default and the Escape key."""
    return confirm_with_option(parent, title, steps, warnings, details, button)[0]


def confirm_with_option(
    parent: QWidget,
    title: str,
    steps: list[str],
    warnings: list[str],
    details: str,
    button: str,
    option: str | None = None,
) -> tuple[bool, bool]:
    """(confirmed, option ticked). The tick box, when given, starts ticked."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(APP_NAME)
    box.setText(title)
    text = "This changes the Pi:\n" + "\n".join(f"• {s}" for s in steps)
    if warnings:
        text += "\n\nCheck first:\n" + "\n".join(f"! {w}" for w in warnings)
    box.setInformativeText(text)
    box.setDetailedText(details)
    tick = None
    if option:
        tick = QCheckBox(option)
        tick.setChecked(True)
        box.setCheckBox(tick)
    confirm = box.addButton(button, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is confirm, bool(tick and tick.isChecked())
