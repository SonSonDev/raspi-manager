"""Table model: one row per configured app (live state merged in as results arrive)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from raspi.core.models import AppRef, AppStatus, Host, HostStatus
from raspi.gui.theme import MONO, STATE_COLOR, STATE_GLYPH, Tokens

COLUMNS = ("State", "App", "Host", "Version", "Since", "Restarts")


@dataclass
class Row:
    host: Host
    app: AppRef | None  # None: host without any configured app
    status: AppStatus | None = None
    host_status: HostStatus | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.host.name, self.app.name if self.app else "")

    def state(self, pending: str | None) -> str:
        if pending:
            return "working"
        if self.app is None:
            return "unknown"
        if self.host_status is None:
            return "checking"
        if not self.host_status.reachable:
            return "unreachable"
        return self.status.state if self.status else "unknown"


class FleetModel(QAbstractTableModel):
    def __init__(self, hosts: list[Host], tokens: Tokens) -> None:
        super().__init__()
        self._hosts = hosts
        self._tokens = tokens
        self._results: dict[str, HostStatus] = {}
        self._pending: dict[tuple[str, str], str] = {}
        self.rows: list[Row] = self._build()

    # --- data in -----------------------------------------------------------
    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._changed()

    def set_hosts(self, hosts: list[Host]) -> None:
        """New configuration (e.g. an env file was declared); live results are kept."""
        self._hosts = hosts
        self._update_rows()

    def set_host_status(self, hs: HostStatus) -> None:
        self._results[hs.host.name] = hs
        self._update_rows()

    def set_pending(self, key: tuple[str, str], label: str | None) -> None:
        if label:
            self._pending[key] = label
        else:
            self._pending.pop(key, None)
        self._changed()

    def _build(self) -> list[Row]:
        rows: list[Row] = []
        for host in self._hosts:
            hs = self._results.get(host.name)
            if not host.apps:
                rows.append(Row(host, None, None, hs))
            by_name = {a.name: a for a in hs.apps} if hs else {}
            rows.extend(Row(host, ref, by_name.get(ref.name), hs) for ref in host.apps)
        return rows

    def _update_rows(self) -> None:
        new = self._build()
        if [r.key for r in new] == [r.key for r in self.rows]:
            self.rows = new
            self._changed()
        else:
            self.beginResetModel()
            self.rows = new
            self.endResetModel()

    def _changed(self) -> None:
        if self.rows:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.rows) - 1, len(COLUMNS) - 1)
            )

    # --- helpers for the window ---------------------------------------------
    def state_at(self, row: int) -> str:
        r = self.rows[row]
        return r.state(self._pending.get(r.key))

    def row_for_key(self, key: tuple[str, str]) -> int:
        return next((i for i, r in enumerate(self.rows) if r.key == key), -1)

    # --- Qt model API -------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = self.rows[index.row()], index.column()
        pending = self._pending.get(row.key)
        state = row.state(pending)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(row, col, state, pending)
        if role == Qt.ItemDataRole.ForegroundRole and col == 0:
            return QBrush(QColor(getattr(self._tokens, STATE_COLOR[state])))
        if role == Qt.ItemDataRole.ForegroundRole and (col >= 3 or row.app is None):
            return QBrush(QColor(self._tokens.muted if col >= 4 else self._tokens.ink))
        if role == Qt.ItemDataRole.FontRole:
            if col == 0:
                font = QFont()
                font.setBold(True)
                return font
            if col >= 3:
                return QFont(MONO.split(",")[0].strip('" '))
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, state)
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 5:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _text(self, row: Row, col: int, state: str, pending: str | None) -> str:
        if col == 0:
            return f"{STATE_GLYPH[state]} {pending or state}"
        if row.app is None:
            return {1: "no app configured", 2: row.host.name}.get(col, "")
        if col == 1:
            return row.app.name
        if col == 2:
            return row.host.name
        s = row.status
        if s is None:
            return "-"
        if col == 3:
            return (s.rev + ("*" if s.dirty else "")) if s.rev else "-"
        if col == 4:
            return s.since or "-"
        return str(s.restarts)

    def _tooltip(self, row: Row, state: str) -> str | None:
        if state == "unreachable" and row.host_status:
            return row.host_status.error
        if row.status and row.status.dirty:
            return f"{row.status.dirty} uncommitted change(s) in the working copy"
        if row.status and row.status.remote:
            return row.status.remote
        return None
