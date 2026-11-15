import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from raspi.core.env import (
    empty_names,
    looks_secret,
    prefill_from_template,
    template_candidates,
)
from raspi.core.models import AppRef, EnvFile, Host
from raspi.core.remote import (
    build_env_detect_script,
    build_template_read_script,
    parse_env_detect,
    parse_env_scan,
    parse_template_read,
)
from raspi.gui import add_env_dialog, env_template
from raspi.gui.add_env_dialog import AddEnvFileDialog
from raspi.gui.env_dialog import EnvDialog
from raspi.services.env import RemoteEnv, read_template, scan_env
from raspi.ssh.transport import RunResult

EXAMPLE = """# Notes App
PORT=3000
DATA_DIR=/mnt/data
BOOTSTRAP_PASSPHRASE=change-me
API_TOKEN=abc123
COOKIE_SECURE=true
OPTIONAL=
"""
SCAN = (
    "@@DIR=/home/pi/apps/notes-app\n./.env.example\n./apps/web/.env.local.bak\n"
)
APP = AppRef("notes-app", "notes-app.service", repo="me/notes-app")
HOST = Host("pi", "10.0.0.1", "pi", (APP,))


class Pi:
    def __init__(self, scan: str = SCAN, template: str | None = EXAMPLE):
        self.scan, self.template, self.scripts = scan, template, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "@@DIR=" in script:
            return RunResult(True, self.scan)
        if "@@TEMPLATE" in script:
            if self.template is None:
                return RunResult(True, "@@NONE\n")
            return RunResult(
                True,
                "@@TEMPLATE /home/pi/apps/notes-app/.env.example\n"
                + self.template,
            )
        return RunResult(True, "@@MISSING\n")  # the env file itself does not exist


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_template_keeps_defaults_and_empties_secrets_and_placeholders():
    values, todo = prefill_from_template(EXAMPLE)
    assert values["PORT"] == "3000" and values["COOKIE_SECURE"] == "true"
    assert values["DATA_DIR"] == "/mnt/data"
    assert values["BOOTSTRAP_PASSPHRASE"] == "" and values["API_TOKEN"] == ""
    assert todo == ["API_TOKEN", "BOOTSTRAP_PASSPHRASE", "OPTIONAL"]
    assert empty_names(values) == todo
    assert looks_secret("session_secret") and not looks_secret("PORT")


def test_template_names_sit_next_to_the_env_file():
    assert template_candidates("/a/.env")[0] == "/a/.env.example"


def test_scan_separates_files_templates_and_backups_and_resolves_home():
    script = build_env_detect_script("$HOME/apps/notes-app")
    assert 'cd "$HOME/apps/notes-app"' in script and "@@DIR=$PWD" in script
    scan = parse_env_scan("$HOME/apps/notes-app", SCAN + "./.env\n")
    assert scan.folder == "/home/pi/apps/notes-app"
    assert scan.files == ["/home/pi/apps/notes-app/.env"]
    assert scan.templates == ["/home/pi/apps/notes-app/.env.example"]
    assert parse_env_detect("/x", "@@NODIR") is None


def test_template_read_script_is_read_only_and_parses():
    script = build_template_read_script("/a/.env")
    for forbidden in ("rm ", ">>", "tee", "sudo", "mv "):
        assert forbidden not in script
    assert parse_template_read("@@NONE\n") is None
    assert parse_template_read("@@TEMPLATE /a/.env.example\nA=1\n") == (
        "/a/.env.example",
        "A=1\n",
    )


def test_services_read_the_scan_and_the_template():
    scan = scan_env(HOST, "$HOME/apps/notes-app", Pi())
    assert scan.templates and not scan.files
    template = read_template(HOST, "/home/pi/apps/notes-app/.env", Pi())
    assert template.todo[0] == "API_TOKEN" and template.values["PORT"] == "3000"
    assert read_template(HOST, "/x/.env", Pi(template=None)) is None


def test_declare_dialog_proposes_the_env_file_to_create(qapp, monkeypatch):
    monkeypatch.setattr(add_env_dialog, "submit", lambda fn, ok, bad=None: ok(fn()))
    dialog = AddEnvFileDialog(HOST, APP, Pi(), set(), set())
    assert dialog.path_box.itemText(0) == "/home/pi/apps/notes-app/.env"
    assert ".env.example" in dialog.hint.text()
    assert dialog.label_edit.text()
    dialog._accept()
    assert dialog.result_value == (
        dialog.label_edit.text(),
        "/home/pi/apps/notes-app/.env",
    )


def test_editor_starts_from_the_template_and_warns_about_empty_values(
    qapp, monkeypatch, tmp_path
):
    monkeypatch.setenv("RASPI_HOME", str(tmp_path))
    env = EnvFile("default", "/home/pi/apps/notes-app/.env")
    app = AppRef("notes-app", "notes-app.service", env_files=(env,))
    monkeypatch.setattr(env_template, "submit", lambda fn, ok, bad=None: ok(fn()))
    dialog = EnvDialog(Host("pi", "10.0.0.1", "pi", (app,)), app, Pi(), None)
    dialog._on_remote(
        (
            "default",
            RemoteEnv(True, False, None),
        )
    )
    assert set(dialog.values()) == {
        "PORT",
        "DATA_DIR",
        "BOOTSTRAP_PASSPHRASE",
        "API_TOKEN",
        "COOKIE_SECURE",
        "OPTIONAL",
    }
    assert "To fill in: API_TOKEN, BOOTSTRAP_PASSPHRASE, OPTIONAL" in dialog.note.text()
    assert dialog._tpl_empty_lines(dialog.values()) == [
        "Without a value: API_TOKEN, BOOTSTRAP_PASSPHRASE, OPTIONAL"
    ]
    dialog._dirty = False
    dialog.close()
