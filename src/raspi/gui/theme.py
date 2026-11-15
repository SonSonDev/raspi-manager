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
    """Only what the native style cannot express: our banners and the monospace logs."""
    return f"""
    QToolBar QToolButton {{ padding: 6px 12px; border: 1px solid transparent; border-radius: 4px; }}
    QToolBar QToolButton:hover {{ background: {t.row_alt}; border-color: {t.rule}; }}
    QToolBar QToolButton:pressed {{ background: {t.selected}; }}
    QToolBar QToolButton:focus {{ border: 2px solid {t.accent}; }}
    QToolBar QToolButton:disabled {{ color: {t.muted}; }}
    QTableView {{ border: 0; selection-background-color: {t.selected}; selection-color: {t.ink}; }}
    QTableView::item {{ padding: 0 8px; border-bottom: 1px solid {t.rule}; }}
    QTableView::item:hover {{ background: {t.row_alt}; }}
    QTableView::item:selected, QTableView::item:selected:!active {{ background: {t.selected}; color: {t.ink}; }}
    QTableView:focus {{ outline: 0; }}
    QLabel#banner {{ color: {t.err}; padding: 8px 12px; border-left: 3px solid {t.err}; }}
    QFrame#setup {{ border-left: 3px solid {t.rule}; }}
    QLabel#setupText {{ padding: 8px 12px; font-family: {MONO}; font-size: 9pt; }}
    QPlainTextEdit {{ font-family: {MONO}; font-size: 9pt; }}
    """
