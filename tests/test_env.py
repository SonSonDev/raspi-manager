import base64
import os
import re
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from raspi.config import envstore
from raspi.config.loader import ConfigError, parse_hosts
from raspi.core.env import EnvError, diff_env, parse_env, render_env
from raspi.core.models import AppRef, EnvFile, Host
from raspi.core.remote import (
    build_env_push_script,
    build_env_read_script,
    parse_env_read,
)
from raspi.gui.env_dialog import MASK, REAL, EnvDialog
from raspi.services.env import push, read_remote
from raspi.ssh.transport import RunResult

SECRET = 's3cr3t value with spaces & "quotes" and # hash'
ENV = EnvFile("default", "/home/u/app/.env")
ENV2 = EnvFile("client", "/home/u/app/client/.env")
APP = AppRef("neko", "neko.service", None, (ENV, ENV2))
HOST = Host("pi", "10.0.0.1", "u", (APP,))


class Transport:
    def __init__(self, stdout="", ok=True, error=""):
        self.scripts, self._r = [], RunResult(ok, stdout, error)

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        return self._r


# --- parsing / rendering -----------------------------------------------------
def test_parse_variants_and_warnings():
    text = "# c\nexport A=1\nB=\"two words\"\nC='raw $x'\nD=val # note\nbad line\nA=2\n"
    p = parse_env(text)
    assert p.values == {"A": "2", "B": "two words", "C": "raw $x", "D": "val # note"}
    assert any("D" in w for w in p.warnings) and any("line 6" in w for w in p.warnings)
    assert any("more than once" in w for w in p.warnings)


def test_render_round_trip_with_awkward_values():
    values = {"PLAIN": "abc-1.2/x", "EMPTY": "", "WEIRD": SECRET, "NL": "a\nb\\c"}
    assert parse_env(render_env(values)).values == values


def test_render_rejects_bad_keys_without_leaking_values():
    with pytest.raises(EnvError) as exc:
        render_env({"1BAD": SECRET})
    assert SECRET not in str(exc.value)
    with pytest.raises(EnvError):
        render_env({"OK": "a\x00b"})


def test_diff():
    d = diff_env({"A": "1", "B": "2", "C": "3"}, {"A": "1", "B": "x", "D": "4"})
    assert (d.added, d.removed, d.changed) == (["D"], ["C"], ["B"])
    assert d.summary() == "+1 ~1 -1" and diff_env({"A": "1"}, {"A": "1"}).empty


# --- remote scripts -------------------------------------------------------------
def test_read_script_and_parse():
    assert "cat /home/u/app/.env" in build_env_read_script("/home/u/app/.env")
    assert parse_env_read("@@EXISTS\nA=1\n") == "A=1\n"
    assert parse_env_read("@@MISSING\n") is None


def test_push_script_carries_content_as_base64_only():
    content = render_env({"TOKEN": SECRET})
    script = build_env_push_script("/home/u/app/.env", content)
    assert SECRET not in script and "TOKEN" not in script
    b64 = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script).group(1)
    assert base64.b64decode(b64).decode() == content
    assert "umask 077" in script and "chmod 600" in script and ".raspi-bak" in script
    assert "mv " in script and "rm -rf" not in script and "sudo" not in script


# --- services -------------------------------------------------------------------------
def test_read_remote_missing_and_existing():
    assert read_remote(HOST, ENV, Transport("@@MISSING\n")).exists is False
    r = read_remote(HOST, ENV, Transport("@@EXISTS\nA=1\n"))
    assert r.exists and r.parsed.values == {"A": "1"}
    assert not read_remote(HOST, ENV, Transport(ok=False, error="down")).ok


def test_push_dry_run_and_errors_do_not_leak_or_execute():
    t = Transport()
    res = push(HOST, ENV, {"TOKEN": SECRET}, t, dry_run=True)
    assert t.scripts == [] and not res.executed and SECRET not in res.message
    bad = push(
        HOST, APP, {"TOKEN": SECRET}, Transport(ok=False, error="Permission denied")
    )
    assert not bad.ok and SECRET not in bad.message


# --- config / store ----------------------------------------------------------------
def test_config_env_file_validation():
    def make(env_file):
        return {
            "hosts": [
                {
                    "name": "p",
                    "address": "1.1.1.1",
                    "user": "u",
                    "apps": [{"name": "a", "unit": "a.service", "env_file": env_file}],
                }
            ]
        }

    assert parse_hosts(make("/home/u/a/.env"))[0].apps[0].env_files == (
        EnvFile("default", "/home/u/a/.env"),
    )
    for bad in ("/etc/passwd", "/home/u/../etc/x", "rel/.env", "/home/u/a b/.env"):
        with pytest.raises(ConfigError):
            parse_hosts(make(bad))


def test_local_store_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    assert envstore.load_local("pi", "neko") is None
    path = envstore.save_local("pi", "neko", {"TOKEN": SECRET})
    assert path.parent == tmp_path / "env" and b"\r\n" not in path.read_bytes()
    assert envstore.load_local("pi", "neko") == {"TOKEN": SECRET}


# --- dialog -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_dialog_masks_values_and_pushes_after_confirmation(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    envstore.save_local("pi", "neko", {"TOKEN": SECRET, "PORT": "2567"})
    transport = Transport("@@EXISTS\nPORT=2567\n")
    dialog = EnvDialog(HOST, APP, transport, None)
    assert pump(lambda: dialog._remote is not None)
    assert (
        dialog.table.item(0, 1).text() == MASK
        and dialog.table.item(0, 1).data(REAL) == SECRET
    )
    assert (
        "differs" in dialog.sync_label.text() and SECRET not in dialog.sync_label.text()
    )
    dialog.show_values.setChecked(True)
    assert dialog.table.item(0, 1).text() == SECRET
    dialog.show_values.setChecked(False)

    # Cancel: nothing is written
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    dialog.push_to_pi(restart=False)
    assert not any("base64 -d" in s for s in transport.scripts)

    # Confirm: pushed once
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == "Push"),
    )
    dialog.push_to_pi(restart=False)
    assert pump(lambda: any("base64 -d" in s for s in transport.scripts))
    pushed = next(s for s in transport.scripts if "base64 -d" in s)
    assert SECRET not in pushed
    dialog._dirty = False
    dialog.close()


def test_dialog_blocks_invalid_names(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dialog = EnvDialog(HOST, APP, Transport("@@MISSING\n"), None)
    assert pump(lambda: dialog._remote is not None)
    dialog._append("1 BAD NAME", "x")
    assert dialog.save_local() is False and warned
    dialog._dirty = False
    dialog.close()


def test_config_multiple_env_files_and_label_rules():
    def make(app_extra):
        return {
            "hosts": [
                {
                    "name": "p",
                    "address": "1.1.1.1",
                    "user": "u",
                    "apps": [{"name": "a", "unit": "a.service", **app_extra}],
                }
            ]
        }

    app = parse_hosts(
        make(
            {
                "env_files": [
                    {"label": "server", "path": "/home/u/a/server/.env"},
                    {"label": "client", "path": "/home/u/a/.env"},
                ]
            }
        )
    )[0].apps[0]
    assert [(f.label, f.path) for f in app.env_files] == [
        ("server", "/home/u/a/server/.env"),
        ("client", "/home/u/a/.env"),
    ]
    assert parse_hosts(make({}))[0].apps[0].env_files == ()
    with pytest.raises(ConfigError):  # duplicate labels
        parse_hosts(
            make(
                {
                    "env_files": [
                        {"label": "x", "path": "/home/u/1"},
                        {"label": "x", "path": "/home/u/2"},
                    ]
                }
            )
        )
    with pytest.raises(ConfigError):  # bad label
        parse_hosts(make({"env_files": [{"label": "a b", "path": "/home/u/1"}]}))
    with pytest.raises(ConfigError):  # system path
        parse_hosts(make({"env_files": [{"label": "x", "path": "/etc/shadow"}]}))


def test_local_store_keeps_one_copy_per_label(monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    envstore.save_local("pi", "neko", {"A": "1"})
    envstore.save_local("pi", "neko", {"B": "2"}, "client")
    assert envstore.load_local("pi", "neko") == {"A": "1"}
    assert envstore.load_local("pi", "neko", "client") == {"B": "2"}
    assert envstore.env_path("pi", "neko").name == "pi.neko.env"
    assert envstore.env_path("pi", "neko", "client").name == "pi.neko.client.env"


class PerFileTransport:
    """Serves a different remote file depending on the path in the read script."""

    def __init__(self):
        self.scripts = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "@@EXISTS" in script and "/client/.env" in script:
            return RunResult(True, "@@EXISTS\nVITE_CLIENT_ID=abc\n")
        if "@@EXISTS" in script:
            return RunResult(True, "@@EXISTS\nPORT=2567\n")
        return RunResult(True, "written: 1 lines")


def test_dialog_switches_between_files_and_pushes_to_the_right_one(
    qapp, monkeypatch, tmp_path
):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    transport = PerFileTransport()
    dialog = EnvDialog(HOST, APP, transport, None)
    assert pump(lambda: dialog._remote is not None)
    assert not dialog.file_picker.isHidden() or dialog.file_picker.count() == 2
    assert "/home/u/app/.env" in dialog.header.text()
    dialog.import_from_pi()
    assert dialog.values() == {"PORT": "2567"}
    dialog._dirty = False

    dialog.file_picker.setCurrentIndex(1)  # client
    assert dialog._env.label == "client" and "/client/.env" in dialog.header.text()
    assert dialog.table.rowCount() == 0  # nothing leaks from the other file
    assert pump(
        lambda: (
            dialog._remote is not None
            and dialog._remote.parsed.values == {"VITE_CLIENT_ID": "abc"}
        )
    )
    dialog.import_from_pi()
    dialog.save_local()
    assert envstore.load_local("pi", "neko", "client") == {"VITE_CLIENT_ID": "abc"}
    assert envstore.load_local("pi", "neko") is None  # default untouched

    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == "Push"),
    )
    dialog.push_to_pi(restart=False)
    assert pump(lambda: any("base64 -d" in s for s in transport.scripts))
    pushed = next(s for s in transport.scripts if "base64 -d" in s)
    assert "target=/home/u/app/client/.env" in pushed
    dialog.close()


def test_stale_read_result_for_previous_file_is_ignored(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    dialog = EnvDialog(HOST, APP, PerFileTransport(), None)
    assert pump(lambda: dialog._remote is not None)
    dialog._env = ENV2
    dialog._remote = None
    dialog._on_remote(("default", read_remote(HOST, ENV, PerFileTransport())))
    assert dialog._remote is None
    dialog._dirty = False
    dialog.close()
