"""Read-only list of the zone's DNS records, filterable."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from raspi.cloudflare.client import CloudflareClient
from raspi.config import cloudflare_settings as store
from raspi.gui.workers import submit
from raspi.services import routing


class DnsRecordsDialog(QDialog):
    """Read-only list of the zone's DNS records, filterable; shows which tunnel a CNAME points to."""

    def __init__(
        self,
        client: CloudflareClient,
        settings: store.CloudflareSettings,
        initial_filter: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._entries: list[routing.DnsEntry] = []
        self.setWindowTitle(f"DNS records - {settings.zone_name}")
        self.resize(900, 520)
        self.filter = QLineEdit(initial_filter)
        self.filter.setPlaceholderText("Filter by name, type or target…")
        self.filter.setAccessibleName("Filter DNS records")
        self.filter.textChanged.connect(self._apply_filter)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Type", "Points to", "Proxied", "Tunnel"]
        )
        self.table.setAccessibleName("DNS records")
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((250, 70, 330, 70)):
            self.table.setColumnWidth(col, width)
        self.status = QLabel("Reading the DNS records…")
        self.status.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Read-only: nothing is created, changed or deleted from this window."
            )
        )
        layout.addWidget(self.filter)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        submit(
            lambda: routing.dns_overview(client, settings),
            self._on_loaded,
            self._on_failed,
        )

    @Slot(object)
    def _on_loaded(self, entries: list[routing.DnsEntry]) -> None:
        self._entries = entries
        self._apply_filter()

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)

    def _apply_filter(self, *_: object) -> None:
        needle = self.filter.text().strip().lower()
        shown = [
            e
            for e in self._entries
            if not needle or needle in f"{e.name} {e.type} {e.content} {e.note}".lower()
        ]
        self.table.setRowCount(0)
        for e in shown:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate(
                (e.name, e.type, e.content, "yes" if e.proxied else "no", e.note)
            ):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.status.setText(f"{len(shown)} of {len(self._entries)} record(s).")
