"""Menu bar and toolbar of the main window.

One QAction per command, shared by the menus and the toolbar (so a shortcut exists once):
  toolbar (one slim line) - what is used every day: Restart, Start, Stop, Logs | Environment, Quick update, Update… | Refresh
  menu Pi                 - look after a Pi (rare)
  menu App                - New app… / Continue setup… (the wizard), then each step on its own, in workflow order, then Remove app…
  menu Cloudflare         - routes and the Cloudflare window
Every command stays one menu away at most; none is hidden deeper. The look is the platform's
(native Windows 11 style): this module only decides what goes where.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QSizePolicy, QToolBar, QWidget


def _action(
    window, attr: str, text: str, slot, shortcut: str = "", tip: str = ""
) -> QAction:
    action = QAction(text, window)
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
    if tip:
        action.setToolTip(tip)
    elif shortcut:
        action.setToolTip(f"{text.rstrip('…')} ({shortcut})")
    action.triggered.connect(slot)
    setattr(window, attr, action)  # the window enables and explains them (`btn_*`)
    return action


def build_chrome(window) -> None:
    a = lambda *args, **kw: _action(window, *args, **kw)

    restart = a("btn_restart", "Restart", lambda: window.act("restart"), "Ctrl+R")
    start = a("btn_start", "Start", lambda: window.act("start"), "Ctrl+S")
    stop = a("btn_stop", "Stop", lambda: window.act("stop"))
    logs = a("btn_logs", "Logs", window.show_logs, "Ctrl+L")
    env = a("btn_env", "Environment…", window.edit_env, "Ctrl+E")
    update = a("btn_update", "Update…", window.update_app_form)
    quick = a(
        "btn_quick_update",
        "Quick update",
        window.quick_update,
        "Ctrl+U",
        "Update to the latest version of the default branch (Ctrl+U); you confirm first",
    )
    now = a(
        "btn_update_now",
        "Update now",
        window.update_now,
        "Ctrl+Shift+U",
        "Update to the latest version with NO confirmation (Ctrl+Shift+U)",
    )
    refresh = a("btn_refresh", "Refresh", window.refresh, "F5")

    new_app = a(
        "btn_new",
        "New app…",
        window.new_app,
        "Ctrl+Shift+N",
        "Set an app up from start to finish: key, clone and build, environment, unit, start",
    )
    resume = a(
        "btn_continue",
        "Continue setup…",
        window.continue_setup,
        tip="Reopen the setup of the selected app where it stopped",
    )
    remove_app = a(
        "btn_remove_app",
        "Remove app…",
        window.remove_app_form,
        tip="Remove the selected app from this Pi: you choose what goes",
    )
    move = a(
        "btn_move",
        "Move to another device…",
        window.move_app_form,
        tip="Stop this app, copy its data and env file to another Pi, start it there",
    )
    key = a(
        "btn_key",
        "Deploy key…",
        window.deploy_key,
        tip="Step 1 of a new app: the read-only GitHub key the Pi clones with",
    )
    keys = a(
        "btn_keys",
        "Manage deploy keys…",
        window.manage_deploy_keys,
        tip="List this Pi's deploy keys: check, copy, rotate or delete them",
    )
    install = a(
        "btn_install",
        "Install app…",
        window.install_app_form,
        tip="Step 2: clone the repository and build it (also updates its settings)",
    )
    unit = a("btn_unit", "Install unit…", window.install_unit_form)
    route = a("btn_route", "Set up route…", window.setup_route)
    cloudflare = a(
        "btn_cloudflare", "Cloudflare…", window.open_cloudflare, "Ctrl+Shift+C"
    )

    tools = a(
        "btn_tools",
        "Install tools…",
        window.install_tools_form,
        tip="Install ffmpeg and yt-dlp on this Pi, for apps that need them",
    )
    check = a(
        "btn_check",
        "Check Pi…",
        window.check_pi,
        tip="Check what this Pi needs to run apps, and install the missing parts",
    )
    scan = a(
        "btn_scan",
        "Scan apps…",
        window.scan_apps,
        tip="Find services already running on this Pi and add them",
    )
    publish = a(
        "btn_publish",
        "Publish apps to Pi…",
        window.publish_apps,
        tip="Copy the app list kept on this PC to the Pi",
    )
    add = a("btn_add_host", "Add a Pi…", window.add_host, "Ctrl+N")
    edit = a("btn_edit_host", "Edit Pi…", window.edit_host, "Ctrl+Shift+E")
    remove = a("btn_remove_host", "Remove Pi…", window.remove_host)

    menus = window.menuBar()
    pi_menu = menus.addMenu("&Pi")
    pi_menu.addActions([check, tools, scan, keys, publish])
    pi_menu.addSeparator()
    pi_menu.addActions([add, edit])
    pi_menu.addSeparator()
    pi_menu.addAction(remove)

    app_menu = menus.addMenu("&App")
    app_menu.addActions([new_app, resume])
    app_menu.addSeparator()
    app_menu.addActions([key, install, env, unit])
    app_menu.addSeparator()
    app_menu.addActions([quick, now, update])
    app_menu.addSeparator()
    app_menu.addAction(move)
    app_menu.addSeparator()
    app_menu.addAction(remove_app)

    cloud_menu = menus.addMenu("&Cloudflare")
    cloud_menu.addActions([route, cloudflare])

    on_top = a(
        "btn_on_top",
        "Keep on top",
        window.set_keep_on_top,
        "Ctrl+T",
        "Keep this window above the others (Ctrl+T)",
    )
    on_top.setCheckable(True)
    on_top.triggered.disconnect()  # a checkable action passes its state, wired below
    on_top.toggled.connect(window.set_keep_on_top)
    menus.addMenu("&View").addAction(on_top)

    bar = QToolBar("Actions")
    bar.setMovable(False)
    window.addToolBar(bar)
    bar.addActions([restart, start, stop, logs])
    bar.addSeparator()
    bar.addActions([env, quick, update])
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    bar.addWidget(spacer)
    bar.addAction(refresh)
