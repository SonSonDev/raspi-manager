"""Start a new environment file from the project's template (`.env.example`) in the editor.

Used by `EnvDialog`: when the file does not exist on the Pi and nothing is saved locally, the
template is read (read-only) and its variables are listed. Defaults the project wrote are kept;
values that look secret or are placeholders start empty and are reported, never invented.
"""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMessageBox

from raspi import APP_NAME
from raspi.config import envstore
from raspi.core.env import empty_names
from raspi.gui.workers import submit
from raspi.services.env import RemoteEnv, TemplateValues, read_template


class EnvTemplateMixin:
    """Mixed into EnvDialog: relies on its `_host`, `_app`, `_env`, `_transport`, `table`,
    `note`, `_fill`, `mark_dirty` and `values`."""

    def _tpl_offer(self, remote: RemoteEnv) -> None:
        if (
            remote.exists
            or self.table.rowCount()
            or envstore.load_local(self._host.name, self._app.name, self._env.label)
            is not None
        ):
            return
        host, env, transport = self._host, self._env, self._transport
        submit(
            lambda: (env.label, read_template(host, env.path, transport)),
            self._tpl_loaded,
            lambda message: None,  # no template is the normal case, not an error
        )

    @Slot(object)
    def _tpl_loaded(self, payload: tuple[str, TemplateValues | None]) -> None:
        label, template = payload
        if label != self._env.label or template is None or self.table.rowCount():
            return  # another file was opened meanwhile, no template, or the user already typed
        self._fill(template.values)
        self.mark_dirty()
        name = template.path.rsplit("/", 1)[-1]
        self.note.setText(
            f"Started from {name}: {len(template.values)} variable(s). "
            + (
                f"To fill in: {', '.join(template.todo)}."
                if template.todo
                else "Every variable has a default."
            )
        )

    def _tpl_empty_lines(self, values: dict[str, str]) -> list[str]:
        """Lines for the push confirmation (names only)."""
        names = empty_names(values)
        return [f"Without a value: {', '.join(names)}"] if names else []

    def _tpl_empty_count(self) -> int:
        return len(empty_names(self.values()))

    def reset_from_template(self) -> None:
        """Replace the editor's variables by the template's. Nothing is pushed until the user does."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText("Reset the variables to the template?")
        box.setInformativeText(
            "The variables in this editor are replaced by the ones in .env.example "
            "(secret-looking names and placeholders start empty). Nothing changes on the Pi "
            "until you push; “Import from Pi…” brings the current file back."
        )
        go = box.addButton("Reset", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        host, env, transport = self._host, self._env, self._transport
        self.note.setText("Reading the template on the Pi…")
        submit(
            lambda: (env.label, read_template(host, env.path, transport)),
            self._tpl_reset_loaded,
            lambda message: self.note.setText(
                f"Could not read the template: {message}"
            ),
        )

    @Slot(object)
    def _tpl_reset_loaded(self, payload: tuple[str, TemplateValues | None]) -> None:
        label, template = payload
        if label != self._env.label:
            return
        if template is None:
            self.note.setText("No template (.env.example) found next to this file.")
            return
        self._fill(template.values)
        self.mark_dirty()
        name = template.path.rsplit("/", 1)[-1]
        self.note.setText(
            f"Reset from {name}: {len(template.values)} variable(s), not pushed yet. "
            + (f"To fill in: {', '.join(template.todo)}." if template.todo else "")
        )
