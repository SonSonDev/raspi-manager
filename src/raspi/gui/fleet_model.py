"""Table model: one row per configured app (live state merged in as results arrive)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from raspi.core.models import (
    AppRef,
    AppStatus,
    Host,
    HostStatus,
    Process,
    ProcessStatus,
)
from raspi.core.stage import Next, next_step, process_next
from raspi.gui.theme import MONO, STATE_COLOR, STATE_GLYPH, Tokens

COLUMNS = ("State", "App", "Host", "Next step", "Version", "Since", "Restarts")
NEXT_COL = 3


@dataclass
class Row:
    host: Host
    app: AppRef | None  # None: host without any configured app
    status: AppStatus | None = None
    host_status: HostStatus | None = None
    process: Process | None = None  # set on the rows of an app with several processes
    pstatus: ProcessStatus | None = None

    @property
    def key(self) -> tuple[str, ...]:
        base = (self.host.name, self.app.name if self.app else "")
        return (*base, self.process.name) if self.process else base

    @property
    def label(self) -> str:
        """What to call this row in messages: the app, or the app and its process."""
        if self.app and self.process:
            return f"{self.app.name} {self.process.name}"
        return self.app.name if self.app else ""

    @property
    def target(self) -> AppRef | None:
        """The app as start/stop/restart/logs see it: its unit is the row's process's."""
        if self.app and self.process:
            return replace(self.app, unit=self.process.unit)
        return self.app

    @property
    def live(self) -> AppStatus | ProcessStatus | None:
        return self.pstatus if self.process else self.status

    def next(self) -> Next:
        if not (self.app and self.status):
            return Next("")
        if self.process:
            return process_next(self.app, self.status, self.process, self.pstatus)
        return next_step(self.app, self.status)

    def state(self, pending: str | None) -> str:
        if pending:
            return "working"
        if self.app is None:
            return "unknown"
        if self.host_status is None:
            return "checking"
        if not self.host_status.reachable:
            return "unreachable"
        return self.live.state if self.live else "unknown"


class FleetModel(QAbstractTableModel):
    def __init__(self, hosts: list[Host], tokens: Tokens) -> None:
        super().__init__()
        self._hosts = hosts
        self._tokens = tokens
        self._results: dict[str, HostStatus] = {}
        self._pending: dict[tuple[str, ...], str] = {}
        self.rows: list[Row] = self._build()

    # --- data in -----------------------------------------------------------
    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._changed()

    def set_hosts(self, hosts: list[Host]) -> None:
        """New configuration (e.g. an env file was declared); live results are kept."""
        self._hosts = hosts
        names = {h.name for h in hosts}
        self._results = {n: r for n, r in self._results.items() if n in names}
        self._update_rows()

    def set_host_status(self, hs: HostStatus) -> None:
        self._results[hs.host.name] = hs
        self._update_rows()

    def host_status(self, name: str) -> HostStatus | None:
        return self._results.get(name)

    def status_summary(self, now: datetime) -> str:
        """Status bar text: last check time, plus why a Pi's apps.yaml was ignored."""
        notes = "; ".join(
            r.inventory_error for r in self._results.values() if r.inventory_error
        )
        return f"Last checked {now:%H:%M:%S}" + (f" - {notes}" if notes else "")

    def set_pending(self, key: tuple[str, ...], label: str | None) -> None:
        if label:
            self._pending[key] = label
        else:
            self._pending.pop(key, None)
        self._changed()

    def _build(self) -> list[Row]:
        rows: list[Row] = []
        for configured in self._hosts:
            hs = self._results.get(configured.name)
            host = hs.host if hs else configured  # the Pi's own app list wins
            if not host.apps:
                rows.append(Row(host, None, None, hs))
            by_name = {a.name: a for a in hs.apps} if hs else {}
            for ref in host.apps:
                status = by_name.get(ref.name)
                if not ref.processes:
                    rows.append(Row(host, ref, status, hs))
                    continue
                live = {p.name: p for p in status.processes} if status else {}
                rows.extend(
                    Row(host, ref, status, hs, p, live.get(p.name))
                    for p in ref.processes
                )
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

    def row_for_key(self, key: tuple[str, ...]) -> int:
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
        if role == Qt.ItemDataRole.ForegroundRole and col == NEXT_COL:
            done = row.next().done
            return QBrush(QColor(self._tokens.muted if done else self._tokens.ink))
        if role == Qt.ItemDataRole.ForegroundRole and (col >= 4 or row.app is None):
            return QBrush(QColor(self._tokens.muted if col >= 5 else self._tokens.ink))
        if role == Qt.ItemDataRole.FontRole:
            if col == 0:
                font = QFont()
                font.setBold(True)
                return font
            if col >= 4:
                return QFont(MONO.split(",")[0].strip('" '))
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, state)
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 6:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _text(self, row: Row, col: int, state: str, pending: str | None) -> str:
        if col == 0:
            return f"{STATE_GLYPH[state]} {pending or state.replace('-', ' ')}"
        if row.app is None:
            return {1: "no app configured", 2: row.host.name}.get(col, "")
        if col == 1:
            return (
                f"{row.app.name} · {row.process.name}" if row.process else row.app.name
            )
        if col == 2:
            return row.host.name
        s = row.status
        if s is None:
            return "-"
        if col == NEXT_COL:
            return row.next().label or "-"
        if col == 4:
            return (s.rev + ("*" if s.dirty else "")) if s.rev else "-"
        live = row.live
        if col == 5:
            return (live.since if live else "") or "-"
        return str(live.restarts if live else 0)

    def _tooltip(self, row: Row, state: str) -> str | None:
        if row.app and row.status and state not in ("unreachable", "working"):
            hint = row.next().why
            if hint:
                return hint
        if state == "unreachable" and row.host_status:
            return row.host_status.error
        if row.status and row.status.dirty:
            return f"{row.status.dirty} uncommitted change(s) in the working copy"
        if row.status and row.status.remote:
            return row.status.remote
        return None
