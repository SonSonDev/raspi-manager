"""Start a new environment file from the project's template (`.env.example`) in the editor.

Used by `EnvDialog`: when the file does not exist on the Pi and nothing is saved locally, the
template is read (read-only) and its variables are listed. Defaults the project wrote are kept;
values that look secret or are placeholders start empty and are reported, never invented.
"""

from __future__ import annotations

from PySide6.QtCore import Slot

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
