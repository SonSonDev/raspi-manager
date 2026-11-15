import base64
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml
from PySide6.QtWidgets import QApplication

from raspi.config.loader import parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, AppStatus, EnvFile, Host
from raspi.core.wizard import CURRENT, DONE, WAITING, current_step, wizard_steps
from raspi.gui import newapp_wizard
from raspi.gui.newapp_wizard import NewAppWizard
from raspi.services.deploykey import ensure_key
from raspi.services.wizard import StatusError, read_status
from raspi.ssh.transport import RunResult

KEYS = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq"]
APP = AppRef(
    "notes-app",
    "notes-app.service",
    repo="me/notes-app",
    build="npm ci",
    run="npm start",
)
HOST = Host("pi", "10.0.0.1", "pi")
ENV = EnvFile("default", "/home/pi/apps/notes-app/.env")


def status(**kw) -> AppStatus:
    base = {
        "name": "notes-app",
        "unit": "notes-app.service",
        "load": "not-found",
        "active": "inactive",
    }
    base.update(kw)
    return AppStatus(**base)


def states(app, st, **kw):
    return [(s.key, s.state) for s in wizard_steps(app, st, **kw)]


# --- the pure part ----------------------------------------------------------------
def test_a_new_app_starts_at_the_key_and_only_one_step_is_current():
    steps = wizard_steps(APP, status())
    assert [s.state for s in steps] == [CURRENT, WAITING, WAITING, WAITING, WAITING]
    assert current_step(steps).key == "key"


def test_a_key_file_alone_is_not_proof_but_a_clone_or_a_check_is():
    assert states(APP, status(key_present=True))[0] == ("key", CURRENT)
    assert states(APP, status(key_present=True), verified=True)[0] == ("key", DONE)
    cloned = status(rev="abc1234")
    assert states(APP, cloned)[0] == ("key", DONE)


def test_clone_without_build_waits_at_the_build_then_environment_then_unit_then_start():
    cloned = status(rev="abc1234")
    assert current_step(wizard_steps(APP, cloned)).key == "install"
    built = AppRef(**{**APP.__dict__, "built": "abc1234"})
    assert current_step(wizard_steps(built, cloned)).key == "env"
    with_env = AppRef(**{**built.__dict__, "env_files": (ENV,)})
    assert (
        current_step(wizard_steps(with_env, cloned)).key == "env"
    )  # declared, not pushed
    pushed = status(rev="abc1234", env_present=(True,))
    assert current_step(wizard_steps(with_env, pushed)).key == "unit"
    unit = status(rev="abc1234", env_present=(True,), load="loaded")
    assert current_step(wizard_steps(with_env, unit)).key == "start"
    running = status(rev="abc1234", env_present=(True,), load="loaded", active="active")
    assert current_step(wizard_steps(with_env, running)) is None


def test_skipping_the_environment_moves_on_and_is_said():
    built = AppRef(**{**APP.__dict__, "built": "abc1234"})
    steps = wizard_steps(built, status(rev="abc1234"), skipped=frozenset({"env"}))
    assert current_step(steps).key == "unit"
    assert "needs none" in next(s for s in steps if s.key == "env").detail


def test_read_status_for_one_app_even_before_it_is_in_the_inventory():
    class Pi:
        def run_script(self, host, script, timeout=20.0):
            return RunResult(True, "@@APP notes-app\nLoadState=not-found\nkey=yes\n")

    assert read_status(HOST, Pi(), APP).key_present is True

    class Down:
        def run_script(self, host, script, timeout=20.0):
            return RunResult(False, "", "timeout")

    with pytest.raises(StatusError, match="timeout"):
        read_status(HOST, Down(), APP)


# --- the whole flow, with a Pi that remembers what was done to it -----------------------
class ScriptedPi:
    def __init__(self, github_accepts: bool = True):
        self.key = self.cloned = self.built = self.unit = self.active = False
        self.env_declared = self.env_pushed = False
        self.github_accepts = github_accepts
        self.manifest: str | None = None
        self.scripts: list[str] = []

    def _app(self) -> AppRef | None:
        if self.manifest is None:
            return None
        return parse_manifest(yaml.safe_load(self.manifest))[0]

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "base64 -d" in script and "apps.yaml" in script:
            encoded = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \|", script)[1]
            self.manifest = base64.b64decode(encoded).decode()
            return RunResult(True, "written\n")
        if "apps.yaml" in script:
            if self.manifest is None:
                return RunResult(True, "@@MISSING\n")
            return RunResult(True, "@@EXISTS\n" + self.manifest)
        if "@@APP " in script:
            lines = [
                "@@APP notes-app",
                "LoadState=" + ("loaded" if self.unit else "not-found"),
                "ActiveState=" + ("active" if self.active else "inactive"),
                "SubState=running" if self.active else "SubState=dead",
                "key=" + ("yes" if self.key else "no"),
                "envfile0=" + ("yes" if self.env_pushed else "no"),
            ]
            if self.cloned:
                lines.append("rev=abc1234")
            return RunResult(True, "\n".join(lines) + "\n")
        if "ssh-keygen -q" in script:
            self.key = True
            return RunResult(True, "pub=ssh-ed25519 AAAA raspi-notes-app@pi\n")
        if "ls-remote" in script:
            if self.key and self.github_accepts:
                return RunResult(True, "reachable\n")
            return RunResult(False, "", "git@github.com: Permission denied (publickey)")
        if "echo state=repo" in script:
            state = "repo" if self.cloned else "missing"
            return RunResult(
                True,
                f"key={'yes' if self.key else 'no'}\nstate={state}\ndisk_mb=9000\n",
            )
        if "git clone" in script or "git rev-parse --short" in script:
            self.cloned = self.built = True
            return RunResult(True, "rev=abc1234\n")
        if 'echo "home=$HOME"' in script:
            return RunResult(
                True,
                "home=/home/pi\nnodebin=/n/bin\nsudo=yes\nunit=missing\nenvfile0=yes\n",
            )
        if "systemctl enable" in script:
            self.unit = True
            return RunResult(True, "installed: notes-app.service\n")
        if "systemctl start" in script:
            self.active = True
            return RunResult(True, "exit=0\nActiveState=active\nSubState=running\n")
        return RunResult(True, "")

    def declare_env(self):
        """What the environment editor does: declare the file and push it."""
        app = self._app()
        declared = AppRef(**{**app.__dict__, "env_files": (ENV,)})
        self.manifest = render_manifest((declared,))
        self.env_pushed = True


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wizard_env(qapp, monkeypatch):
    shown = {"errors": [], "keys": []}
    monkeypatch.setattr(
        newapp_wizard, "submit", lambda fn, ok, bad=None: _inline(fn, ok, bad)
    )
    monkeypatch.setattr(newapp_wizard, "confirm_change", lambda *a: True)
    monkeypatch.setattr(
        newapp_wizard.QMessageBox, "critical", lambda *a: shown["errors"].append(a[2])
    )
    monkeypatch.setattr(
        newapp_wizard,
        "ensure_key",
        lambda host, t, name, dry_run=False: ensure_key(
            host, t, name, dry_run, lookup=lambda: KEYS
        ),
    )
    monkeypatch.setattr(
        NewAppWizard,
        "_show_public_key",
        lambda self, key, problem: shown["keys"].append(key),
    )
    return shown


def _inline(fn, on_done, on_failed):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        on_failed(str(exc))
        return
    on_done(result)


def _current(wizard):
    step = current_step(wizard._steps)
    return step.key if step else None


def test_the_wizard_takes_a_new_app_from_nothing_to_running(wizard_env, monkeypatch):
    pi = ScriptedPi()
    monkeypatch.setattr(
        NewAppWizard, "_do_env", lambda self: (pi.declare_env(), self.refresh())
    )
    wizard = NewAppWizard(HOST, APP, pi)
    seen = []
    while _current(wizard):
        seen.append(_current(wizard))
        wizard.run_current()
        assert not wizard_env["errors"], wizard_env["errors"]
    assert seen == ["key", "install", "env", "unit", "start"]
    assert "set up and running" in wizard.note.text()
    assert not wizard.run_btn.isEnabled()
    saved = pi._app()
    assert (
        saved.built == "abc1234"
        and saved.env_files == (ENV,)
        and saved.run == "npm start"
    )
    wizard.close()


def test_a_key_github_does_not_accept_yet_keeps_the_wizard_on_that_step(wizard_env):
    pi = ScriptedPi(github_accepts=False)
    wizard = NewAppWizard(HOST, APP, pi)
    wizard.run_current()
    assert _current(wizard) == "key" and wizard_env["keys"]  # the public key was shown
    assert "GitHub" in wizard.note.text()
    assert not pi.cloned
    wizard.close()


def test_a_failed_build_leaves_nothing_recorded_and_the_wizard_stays_there(wizard_env):
    pi = ScriptedPi()
    wizard = NewAppWizard(HOST, APP, pi)
    wizard.run_current()  # key
    original = pi.run_script

    def failing(host, script, timeout=20.0):
        if "git clone" in script:
            pi.scripts.append(script)
            pi.cloned = True
            return RunResult(False, "", "npm ERR! build failed")
        return original(host, script, timeout)

    pi.run_script = failing
    wizard.run_current()  # install fails
    assert wizard_env["errors"] and "build failed" in wizard_env["errors"][0]
    assert pi.manifest is None  # nothing recorded
    assert _current(wizard) == "install"  # cloned but not built: it resumes here
    wizard.close()


def test_reopening_on_a_half_set_up_app_resumes_where_it_stopped(wizard_env):
    pi = ScriptedPi()
    pi.key = pi.cloned = pi.built = True
    pi.manifest = render_manifest((AppRef(**{**APP.__dict__, "built": "abc1234"}),))
    host = Host("pi", "10.0.0.1", "pi", (pi._app(),))
    wizard = NewAppWizard(host, pi._app(), pi)
    assert _current(wizard) == "env"
    assert wizard.skip_btn.isVisibleTo(wizard)
    wizard._skip_env()
    assert _current(wizard) == "unit"
    wizard.close()


def test_the_window_offers_the_wizard(qapp):
    from raspi.gui.main_window import MainWindow

    assert hasattr(MainWindow, "new_app") and hasattr(MainWindow, "continue_setup")
