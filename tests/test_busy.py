import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from raspi.core.models import Host
from raspi.core.tools import APT_WAIT, YtDlpDownload
from raspi.gui import busy, tools_actions
from raspi.gui.main_window import MainWindow
from raspi.gui.theme import LIGHT
from raspi.services.tools import plan_tools
from raspi.ssh.transport import RunResult

HOST = Host("pi", "10.0.0.1", "pi")
FILE = YtDlpDownload("2026.10.26", "yt-dlp_linux_aarch64", "file", "e" * 64)


class Pi:
    def __init__(self):
        self.scripts = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "uname -m" in script:
            return RunResult(
                True,
                "machine=aarch64\nsudo=yes\ncurl=yes\nunzip=yes\nffmpeg=\nytdlp=\n",
            )
        return RunResult(
            True, "ffmpeg=ffmpeg version 7.1 Copyright\nyt-dlp=2026.10.26\n"
        )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean():
    busy.end("pi")
    yield
    busy.end("pi")


def test_one_change_at_a_time_per_pi():
    assert busy.begin("pi") is True
    assert busy.running("pi") and busy.begin("pi") is False
    assert busy.begin("other") is True  # another Pi is free
    busy.end("pi")
    busy.end("other")
    assert not busy.running("pi")


def test_every_apt_call_waits_for_the_lock_instead_of_failing():
    from raspi.core.provision import build_provision_script
    from raspi.core.tools import build_tools_script

    assert APT_WAIT == "-o DPkg::Lock::Timeout=600"
    for script in (
        build_provision_script(["git"], False),
        build_tools_script(True, None, []),
    ):
        assert f"apt-get {APT_WAIT} update" in script
        assert f"apt-get install -y {APT_WAIT}" in script


def test_a_second_install_on_the_same_pi_is_refused_and_runs_nothing(qapp, monkeypatch):
    shown = []
    pending = []
    monkeypatch.setattr(
        tools_actions, "submit", lambda fn, ok, bad=None: pending.append((fn, ok))
    )
    monkeypatch.setattr(tools_actions, "confirm_change", lambda *a: True)
    monkeypatch.setattr(
        tools_actions.QMessageBox, "information", lambda *a: shown.append(a[2])
    )
    pi = Pi()
    window = MainWindow([HOST], pi, LIGHT, auto_refresh=False)
    plan = plan_tools(HOST, pi, {"ffmpeg"}, lambda m: FILE)
    window._on_tools_planned(
        (HOST, plan)
    )  # first click: the install starts (not finished yet)
    assert len(pending) == 1 and busy.running("pi")
    window._on_tools_planned((HOST, plan))  # second click while the first runs
    assert len(pending) == 1 and shown and "already running" in shown[0]
    fn, done = pending[0]
    done(fn())  # the first one finishes: the Pi is free again
    assert not busy.running("pi")
    window.close()
