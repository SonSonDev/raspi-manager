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
    confirmed, ticks = confirm_with_options(
        parent, title, steps, warnings, details, button, [option] if option else []
    )
    return confirmed, bool(ticks and ticks[0])


def confirm_with_options(
    parent: QWidget,
    title: str,
    steps: list[str],
    warnings: list[str],
    details: str,
    button: str,
    options: list[str],
) -> tuple[bool, list[bool]]:
    """(confirmed, one tick per option). Every tick box starts ticked."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(APP_NAME)
    box.setText(title)
    text = "This changes the Pi:\n" + "\n".join(f"• {s}" for s in steps)
    if warnings:
        text += "\n\nCheck first:\n" + "\n".join(f"! {w}" for w in warnings)
    box.setInformativeText(text)
    box.setDetailedText(details)
    ticks = [QCheckBox(label) for label in options]
    for tick in ticks:
        tick.setChecked(True)
    if ticks:
        box.setCheckBox(ticks[0])
    grid = box.layout()
    for extra in ticks[
        1:
    ]:  # a message box has room for one tick box: add the others below
        grid.addWidget(extra, grid.rowCount(), 0, 1, grid.columnCount())
    confirm = box.addButton(button, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is confirm, [t.isChecked() for t in ticks]
