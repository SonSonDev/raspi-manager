"""Every shell script raspi-manager sends to a Pi must at least parse, and stay POSIX.

The Pi runs them with `sh` (dash on Raspberry Pi OS). A syntax error or a bashism would only show up
on the real Pi, in the middle of a change, so each generated script is checked here with `sh -n`
(nothing is executed).
"""

import re
import shutil
import subprocess

import pytest

from raspi.core import (
    connector,
    deploykey,
    install,
    provision,
    remote,
    runtime,
    scan,
    unit,
    update,
)
from raspi.core.models import AppRef, EnvFile, Requirement

APP = AppRef(
    "notes-app",
    "notes-app.service",
    path="/home/pi/apps/notes-app",
    env_files=(EnvFile("default", "/home/pi/apps/notes-app/.env"),),
    repo="SonSonDev/notes-app",
    requires=(
        Requirement("mount", "/mnt/data"),
        Requirement("file", "/mnt/data/.marker"),
        Requirement("folder", "/mnt/data/db"),
    ),
    build="npm ci && npm run build",
    run="npm start",
)
APP_NO_PATH = AppRef("game", "game.service", repo="me/game", build="make", run="./run")
DOWNLOAD = connector.Download("2026.11.0", "cloudflared-linux-arm64", "a" * 64)
KEYS = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq", "ecdsa-sha2-nistp256 AAAAE2Vj/+="]

SCRIPTS = {
    "status": remote.build_status_script((APP, APP_NO_PATH)),
    "action": remote.build_action_script("a.service", "restart"),
    "logs": remote.build_logs_script("a.service", 50),
    "env read": remote.build_env_read_script("/home/pi/app/.env"),
    "manifest read": remote.build_manifest_read_script(),
    "manifest push": remote.build_manifest_push_script("version: 1\napps: []\n"),
    "env detect": remote.build_env_detect_script("/home/pi/app"),
    "env detect default dir": remote.build_env_detect_script("$HOME/apps/notes-app"),
    "env template read": remote.build_template_read_script("/home/pi/app/.env"),
    "env push": remote.build_env_push_script("/home/pi/app/.env", "A=1\n"),
    "scan": scan.build_scan_script(),
    "doctor": provision.build_check_script(),
    "provision": provision.build_provision_script(["git", "curl"], True),
    "provision dir only": provision.build_provision_script([], True),
    "node with nvm": runtime.build_node_script("22", True),
    "node without nvm": runtime.build_node_script("22", False),
    "deploy key": deploykey.build_key_script("notes-app", "raspi-v", KEYS),
    "deploy key verify": deploykey.build_verify_script("notes-app", "me/repo"),
    "install probe": install.build_probe_script(APP),
    "install probe default dir": install.build_probe_script(APP_NO_PATH),
    "clone and build": install.build_install_script(APP, "missing"),
    "build existing": install.build_install_script(APP_NO_PATH, "repo"),
    "unit probe": unit.build_probe_script(APP),
    "update probe": update.build_probe_script(APP),
    "update probe ref": update.build_probe_script(APP, ref="v1.2/x"),
    "update probe sha": update.build_probe_script(APP, sha="abc1234"),
    "update": update.build_update_script(APP, "b" * 40),
    "unit install": unit.build_install_script(
        APP, unit.render_unit(APP, "pi", "/home/pi/apps/notes-app", "/n/bin")
    ),
    "connector check": connector.build_check_script("raspi-tunnel-x.service"),
    "connector install": connector.build_install_script(
        "raspi-tunnel-x.service", "[Unit]\n", "tok", DOWNLOAD
    ),
    "connector install, no download": connector.build_install_script(
        "raspi-tunnel-x.service", "[Unit]\n", "tok", None
    ),
    "ports": connector.build_ports_script("a.service"),
}
BASHISMS = {
    "[[": re.compile(r"\[\["),
    "here-string": re.compile(r"<<<"),
    "function keyword": re.compile(r"^\s*function\s", re.MULTILINE),
    "$'...'": re.compile(r"\$'"),
    "process substitution": re.compile(r"<\("),
    "[ a == b ]": re.compile(r"\[ [^\]]*==[^\]]*\]"),
}
SH = shutil.which("dash") or shutil.which("sh")


@pytest.mark.skipif(SH is None, reason="no local sh")
@pytest.mark.parametrize("name", SCRIPTS)
def test_script_parses(name):
    result = subprocess.run(
        [SH, "-n"], input=SCRIPTS[name].encode(), capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_has_no_bashism(name):
    found = [
        label for label, pattern in BASHISMS.items() if pattern.search(SCRIPTS[name])
    ]
    assert not found, f"{name}: {found}"
