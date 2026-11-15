"""Design tokens from docs/DESIGN.md ("Ops Ledger") as a Qt style sheet."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tokens:
    bg: str
    ink: str
    muted: str
    rule: str
    accent: str
    on_accent: str
    ok: str
    warn: str
    err: str
    info: str
    row_alt: str
    selected: str


LIGHT = Tokens(
    bg="#FFFFFF",
    ink="#111418",
    muted="#5B6168",
    rule="#D5D8DC",
    accent="#0B57D0",
    on_accent="#FFFFFF",
    ok="#1B6E3A",
    warn="#8A5A00",
    err="#B3261E",
    info="#5B6168",
    row_alt="#F6F7F8",
    selected="#E4EEFC",
)
DARK = Tokens(
    bg="#0E1013",
    ink="#E7E9EC",
    muted="#9AA1A9",
    rule="#2A2F35",
    accent="#7AA7FF",
    on_accent="#0E1013",
    ok="#5FD08A",
    warn="#E3B341",
    err="#FF8A80",
    info="#9AA1A9",
    row_alt="#14171B",
    selected="#1A2740",
)

SANS = '"IBM Plex Sans", "Segoe UI", sans-serif'
MONO = '"IBM Plex Mono", "Cascadia Mono", Consolas, monospace'

STATE_COLOR = {
    "running": "ok",
    "stopped": "muted",
    "failed": "err",
    "working": "info",
    "paused": "warn",
    "unreachable": "err",
    "checking": "info",
    "unknown": "muted",
    "no-unit": "muted",
}
STATE_GLYPH = {
    "running": "●",
    "stopped": "○",
    "failed": "▲",
    "working": "◌",
    "paused": "‖",
    "unreachable": "✕",
    "checking": "◌",
    "unknown": "?",
    "no-unit": "–",
}


def stylesheet(t: Tokens) -> str:
    return f"""
    * {{ font-family: {SANS}; font-size: 10pt; }}
    QMainWindow, QWidget {{ background: {t.bg}; color: {t.ink}; }}
    QToolBar {{ border: 0; border-bottom: 1px solid {t.rule}; spacing: 6px; padding: 6px 8px; }}
    QToolButton {{ border: 1px solid {t.rule}; border-radius: 2px; padding: 4px 12px; min-height: 22px; background: {t.bg}; color: {t.ink}; }}
    QToolButton:hover {{ border-color: {t.accent}; }}
    QToolButton:focus {{ border: 2px solid {t.accent}; }}
    QToolButton:disabled {{ color: {t.muted}; border-color: {t.rule}; }}
    QToolButton#primary {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}
    QToolButton#primary:disabled {{ background: {t.bg}; color: {t.muted}; border-color: {t.rule}; }}
    QToolButton#danger {{ color: {t.err}; }}
    QToolButton#danger:disabled {{ color: {t.muted}; }}
    QPushButton {{ border: 1px solid {t.rule}; border-radius: 2px; padding: 4px 12px; min-height: 22px; background: {t.bg}; color: {t.ink}; }}
    QPushButton:hover {{ border-color: {t.accent}; }}
    QPushButton:focus {{ border: 2px solid {t.accent}; }}
    QPushButton:disabled {{ color: {t.muted}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t.muted}; border-radius: 2px; background: {t.bg}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QCheckBox:focus {{ outline: 0; }}
    QCheckBox::indicator:focus {{ border: 2px solid {t.accent}; }}
    QLineEdit, QComboBox {{ border: 1px solid {t.muted}; border-radius: 2px; padding: 2px 6px; background: {t.bg}; color: {t.ink}; }}
    QLineEdit:focus, QComboBox:focus {{ border: 2px solid {t.accent}; }}
    QTableView {{ border: 0; gridline-color: {t.rule}; selection-background-color: {t.selected}; selection-color: {t.ink}; alternate-background-color: {t.bg}; }}
    QTableView::item {{ padding: 0 8px; border-bottom: 1px solid {t.rule}; }}
    QTableView::item:hover {{ background: {t.row_alt}; }}
    QTableView::item:selected, QTableView::item:selected:!active {{ background: {t.selected}; color: {t.ink}; }}
    QTableView:focus {{ outline: 0; }}
    QHeaderView::section {{ background: {t.bg}; color: {t.muted}; border: 0; border-bottom: 1px solid {t.rule}; padding: 4px 8px; font-weight: 600; font-size: 9pt; text-align: left; }}
    QStatusBar {{ border-top: 1px solid {t.rule}; color: {t.muted}; }}
    QStatusBar::item {{ border: 0; }}
    QPlainTextEdit {{ font-family: {MONO}; font-size: 9pt; border: 0; border-top: 1px solid {t.rule}; background: {t.bg}; }}
    QDockWidget {{ font-weight: 600; }}
    QDockWidget::title {{ padding: 6px 8px; border-bottom: 1px solid {t.rule}; background: {t.bg}; }}
    QLabel#banner {{ color: {t.err}; padding: 8px 12px; border-left: 3px solid {t.err}; }}
    QFrame#setup {{ border-left: 3px solid {t.rule}; }}
    QLabel#setupText {{ padding: 8px 12px; font-family: {MONO}; font-size: 9pt; }}
    QScrollBar:vertical {{ background: {t.bg}; width: 12px; }}
    QScrollBar::handle:vertical {{ background: {t.rule}; min-height: 24px; border-radius: 2px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    """
